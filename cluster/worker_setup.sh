#!/bin/bash
# Worker Node Setup Script for Ray Cluster
# Run this on your Ubuntu Server machines to configure them as Ray worker nodes

set -e  # Exit immediately if a command exits with a non-zero status

# Check if the head node IP was provided
if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <head-node-ip>"
    echo "Example: $0 192.168.1.100"
    exit 1
fi

HEAD_NODE_IP=$1

echo "========================================================"
echo "Setting up Ray Cluster Worker Node on Ubuntu Server"
echo "Head Node IP: $HEAD_NODE_IP"
echo "========================================================"

# Check if running with sudo privileges
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root or with sudo"
  exit 1
fi

# Get the username of the user who invoked sudo
if [ -n "$SUDO_USER" ]; then
  USERNAME="$SUDO_USER"
else
  # Prompt for username if not run with sudo
  read -p "Enter your username: " USERNAME
fi

echo "Setting up for user: $USERNAME"

# Update package lists (continue even if there are errors with some repositories)
echo "Updating package lists..."
apt-get update || true

# Install required packages
echo "Installing required packages..."
apt-get install -y \
  python3-pip \
  python3-venv \
  python3-full \
  python3-dev \
  tmux \
  htop \
  git \
  build-essential \
  openssh-server \
  curl \
  wget \
  apt-transport-https \
  ca-certificates \
  gnupg \
  lsb-release \
  netcat-openbsd

# Install Docker for node-exporter
echo "Installing Docker..."
if ! command -v docker &> /dev/null; then
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
  apt-get update || true
  apt-get install -y docker-ce docker-ce-cli containerd.io
  
  # Add user to the docker group
  usermod -aG docker $USERNAME
  echo "Docker installed."
else
  echo "Docker already installed."
fi

# Create ray-cluster directory
echo "Creating required directories..."
mkdir -p /home/$USERNAME/ray-cluster
chown -R $USERNAME:$USERNAME /home/$USERNAME/ray-cluster

# Test connectivity to head node
echo "Testing connectivity to head node..."
if ping -c 3 $HEAD_NODE_IP &> /dev/null; then
  echo "Network connectivity to head node confirmed."
else
  echo "WARNING: Cannot ping head node at $HEAD_NODE_IP. Please check network connectivity."
  echo "Continuing with setup, but worker may not connect properly."
fi

# Test Ray port connectivity
echo "Testing Ray port connectivity..."
if nc -z -w 5 $HEAD_NODE_IP 6379; then
  echo "Ray port connectivity confirmed."
else
  echo "WARNING: Cannot connect to Ray port on head node at $HEAD_NODE_IP:6379."
  echo "Please ensure the head node has Ray running and port 6379 is open."
  echo "Continuing with setup, but worker may not connect properly."
fi

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

# Set up Python virtual environment
echo "Setting up Python virtual environment..."
su - $USERNAME -c "python3 -m venv /home/$USERNAME/ray-env"

# Install Ray in the virtual environment
echo "Installing Ray..."
su - $USERNAME -c "source /home/$USERNAME/ray-env/bin/activate && pip install --upgrade pip"
# Install a stable version with plasma store disabled
su - $USERNAME -c "source /home/$USERNAME/ray-env/bin/activate && pip install ray[default]==2.44.1"

# Create a Ray configuration file to disable object store
echo "Configuring Ray to disable object store..."
mkdir -p /home/$USERNAME/.ray
cat > /home/$USERNAME/.ray/ray.yaml << EOF
# Configuration for Ray with minimal memory usage
object_store_memory: 100000000  # 100MB only, minimal object store
plasma_directory: /tmp
EOF
chown -R $USERNAME:$USERNAME /home/$USERNAME/.ray

# Configure firewall
echo "Configuring firewall..."
ufw allow 22/tcp
ufw allow 6379/tcp      # Ray client server
ufw allow 10001/tcp     # Ray internal communication
ufw allow 9100/tcp      # Node exporter for Prometheus
ufw --force enable

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

# Start Ray worker with direct connection to head node
echo "Starting Ray worker with connection to $HEAD_NODE_IP:6379"
ray start --address=$HEAD_NODE_IP:6379 --num-cpus=4 --block
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
ray status
EOF

chmod +x /home/$USERNAME/ray-cluster/check_ray.sh
chown $USERNAME:$USERNAME /home/$USERNAME/ray-cluster/check_ray.sh

# Stop the systemd service that's not working
echo "Stopping and disabling systemd Ray worker service..."
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