#!/bin/bash
# Worker Node Setup Script for Ray Cluster
# Run this on your Ubuntu Server machines to configure them as Ray worker nodes

# Exit on error
set -e

# Check if user provided the head node IP as an argument
if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <head_node_ip>"
    exit 1
fi

HEAD_NODE_IP=$1

# Get the username of the current user (for file permissions)
USERNAME=$(logname || echo ${SUDO_USER:-$USER})
echo "Setting up Ray worker node for user: $USERNAME"

# Install required packages
echo "Installing required packages..."
apt-get update
apt-get install -y python3-venv python3-pip tmux htop git curl wget lsof net-tools netcat-openbsd

# Configure firewall
echo "Configuring firewall..."
apt-get install -y ufw
ufw allow 22/tcp
ufw allow 6379/tcp      # Ray client server
ufw allow 10001/tcp     # Ray internal communication
ufw allow 9100/tcp      # Node exporter for Prometheus
ufw --force enable

# Set up Python virtual environment
echo "Setting up Python virtual environment..."
mkdir -p /home/$USERNAME/ray-cluster
mkdir -p /home/$USERNAME/ray-cluster/logs
chown -R $USERNAME:$USERNAME /home/$USERNAME/ray-cluster

# Create Python venv
if [ ! -d "/home/$USERNAME/ray-env" ]; then
    su - $USERNAME -c "python3 -m venv /home/$USERNAME/ray-env"
fi

# Install Ray in the virtual environment
echo "Installing Ray..."
su - $USERNAME -c "source /home/$USERNAME/ray-env/bin/activate && pip install --upgrade pip"
# Install a stable version with plasma store disabled
su - $USERNAME -c "source /home/$USERNAME/ray-env/bin/activate && pip install ray[default]==2.44.1"

# Create a shared memory directory that can be used by Ray
echo "Setting up shared memory for Ray..."
if ! grep -q "/dev/shm" /etc/fstab; then
  echo "Configuring shared memory mount..."
  echo "tmpfs /dev/shm tmpfs defaults,size=1G 0 0" >> /etc/fstab
  mount -o remount /dev/shm
fi

# Increase shared memory limits
cat > /etc/sysctl.d/90-ray-shared-memory.conf << EOF
# Increase shared memory limits
kernel.shmmax=2147483648
kernel.shmall=2097152
EOF
sysctl -p /etc/sysctl.d/90-ray-shared-memory.conf

# Create a Ray configuration file
echo "Configuring Ray..."
mkdir -p /home/$USERNAME/.ray
cat > /home/$USERNAME/.ray/ray.yaml << EOF
# Ray configuration with proper memory settings
object_store_memory: 500000000  # 500MB object store
plasma_directory: /tmp
logging:
  logs_dir: "/home/$USERNAME/ray-cluster/logs"
  logs_rotation_max_bytes: 100000000
  logs_rotation_backup_count: 5
EOF
chown -R $USERNAME:$USERNAME /home/$USERNAME/.ray

# Optimize network settings for Ray
echo "Optimizing network settings for Ray..."
cat > /etc/sysctl.d/99-ray-network.conf << EOF
# Increase the maximum socket buffer size
net.core.rmem_max=16777216
net.core.wmem_max=16777216

# Increase the default socket buffer size
net.core.rmem_default=262144
net.core.wmem_default=262144

# Increase the maximum number of connections
net.core.somaxconn=65535

# Increase the maximum number of packets queued
net.core.netdev_max_backlog=2000

# Increase TCP keepalive for better connection stability (crucial for Ray heartbeats)
net.ipv4.tcp_keepalive_time=30
net.ipv4.tcp_keepalive_intvl=5
net.ipv4.tcp_keepalive_probes=10

# TCP congestion control
net.ipv4.tcp_congestion_control=cubic
net.ipv4.tcp_slow_start_after_idle=0

# Avoid dropping connections
net.ipv4.tcp_retries2=15

# Increase maximum active socket connections
net.ipv4.tcp_max_tw_buckets=1440000
net.ipv4.tcp_fin_timeout=15

# Increase TCP window scaling window
net.ipv4.tcp_window_scaling=1
EOF

# Apply the network settings
echo "Applying network settings..."
sysctl --system || echo "Warning: Could not apply all sysctl settings. This is not critical."

# Create a simplified direct run script for Ray worker
echo "Creating direct Ray worker run script..."
cat > /home/$USERNAME/ray-cluster/direct_worker.sh << EOF
#!/bin/bash

# Stop any existing Ray processes
source /home/$USERNAME/ray-env/bin/activate
ray stop 2>/dev/null || true
sleep 2

# Clean up Ray directory
rm -rf /tmp/ray
mkdir -p /tmp/ray
chmod 777 /tmp/ray

# Create log directory
mkdir -p ~/ray-cluster/logs

# Set environment variables for debugging
export RAY_RAYLET_VERBOSITY=4
export RAY_verbose_spill_logs=0
export RAY_record_ref_creation_sites=0

# Start Ray worker with direct connection to head node and capture logs
echo "Starting Ray worker with connection to $HEAD_NODE_IP:6379"
TOTAL_MEMORY=$(free -b | grep "Mem:" | awk '{print $2}')
OBJECT_STORE_MEMORY=$((TOTAL_MEMORY / 4))  # 25% of system memory

ray start --address=$HEAD_NODE_IP:6379 \
  --num-cpus=2 \
  --object-store-memory=$OBJECT_STORE_MEMORY \
  --plasma-directory=/tmp \
  --log-to-driver \
  --logging-level=debug \
  --block 2>&1 | tee ~/ray-cluster/logs/raylet-worker-$(date +%Y%m%d-%H%M%S).log
EOF

chmod +x /home/$USERNAME/ray-cluster/direct_worker.sh
chown $USERNAME:$USERNAME /home/$USERNAME/ray-cluster/direct_worker.sh

# Create a tmux launcher script
echo "Creating tmux launcher script..."
cat > /home/$USERNAME/ray-cluster/start_ray_tmux.sh << EOF
#!/bin/bash

# Kill existing tmux session if it exists
tmux kill-session -t ray-worker 2>/dev/null || true

# Start a new tmux session
tmux new-session -d -s ray-worker

# Run the Ray worker script in the tmux session
tmux send-keys -t ray-worker "cd /home/$USERNAME && ./ray-cluster/direct_worker.sh" C-m

echo "Ray worker started in tmux session 'ray-worker'"
echo "To view the session, run: tmux attach -t ray-worker"
echo "To detach from the session (keep it running), press Ctrl+B then D"
EOF

chmod +x /home/$USERNAME/ray-cluster/start_ray_tmux.sh
chown $USERNAME:$USERNAME /home/$USERNAME/ray-cluster/start_ray_tmux.sh

# Create a simple helper script to check Ray status
cat > /home/$USERNAME/ray-cluster/check_ray.sh << EOF
#!/bin/bash
source /home/$USERNAME/ray-env/bin/activate
ray status --address=$HEAD_NODE_IP:6379
EOF

chmod +x /home/$USERNAME/ray-cluster/check_ray.sh
chown $USERNAME:$USERNAME /home/$USERNAME/ray-cluster/check_ray.sh

# Stop any systemd service that might be running
echo "Stopping any systemd Ray worker service..."
systemctl stop ray-worker.service 2>/dev/null || true
systemctl disable ray-worker.service 2>/dev/null || true

# Set up a cron job to auto-start the worker on reboot
echo "Setting up cron job to auto-start Ray worker on reboot..."
CRON_JOB="@reboot /home/$USERNAME/ray-cluster/start_ray_tmux.sh > /home/$USERNAME/ray-cluster/startup.log 2>&1"
(crontab -u $USERNAME -l 2>/dev/null || echo "") | grep -v "start_ray_tmux.sh" | { cat; echo "$CRON_JOB"; } | crontab -u $USERNAME -

# Run the Ray worker in tmux directly
echo "Starting Ray worker in tmux session..."
su - $USERNAME -c "/home/$USERNAME/ray-cluster/start_ray_tmux.sh"
sleep 5

# Final instructions
echo "========================================================"
echo "Ray worker node setup completed with TMUX approach!"
echo "========================================================"
echo ""
echo "STATUS INFORMATION:"
echo "- Ray worker is running in a tmux session (not as a service)"
echo "- To view the Ray worker: tmux attach -t ray-worker"
echo ""
echo "MANAGEMENT COMMANDS:"
echo "- Start worker:  ~/ray-cluster/start_ray_tmux.sh"
echo "- Check status:  ~/ray-cluster/check_ray.sh"
echo "- Stop worker:   source ~/ray-env/bin/activate && ray stop"
echo ""
echo "The worker will automatically start on reboot via cron."
echo "========================================================"