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

# Increase the maximum number of open files
fs.file-max=1048576

# Increase the maximum number of connection tracking entries
net.netfilter.nf_conntrack_max=1048576

# Increase the TCP max buffer size
net.ipv4.tcp_rmem=4096 87380 16777216
net.ipv4.tcp_wmem=4096 65536 16777216

# Enable TCP window scaling
net.ipv4.tcp_window_scaling=1

# Increase the TCP maximum and default buffer sizes
net.ipv4.tcp_mem=16777216 16777216 16777216

# Disable slow start after idle
net.ipv4.tcp_slow_start_after_idle=0
EOF

# Apply the network settings
sysctl --system

# Set up Python virtual environment
echo "Setting up Python virtual environment..."
su - $USERNAME -c "python3 -m venv /home/$USERNAME/ray-env"

# Install Ray in the virtual environment
echo "Installing Ray..."
su - $USERNAME -c "source /home/$USERNAME/ray-env/bin/activate && pip install --upgrade pip"
su - $USERNAME -c "source /home/$USERNAME/ray-env/bin/activate && pip install 'ray[default]' pandas numpy psutil prometheus-client"

# Configure firewall
echo "Configuring firewall..."
ufw allow 22/tcp
ufw allow 6379/tcp      # Ray client server
ufw allow 10001/tcp     # Ray internal communication
ufw allow 9100/tcp      # Node exporter for Prometheus
ufw --force enable

# Create a script to start Ray worker node
echo "Creating Ray worker start script..."
cat > /home/$USERNAME/ray-cluster/start_worker.sh << EOF
#!/bin/bash
source /home/$USERNAME/ray-env/bin/activate

# Make sure any previous Ray instances are stopped
ray stop

# Make sure /tmp/ray is clean
rm -rf /tmp/ray

# Configure system for Ray
echo "net.core.rmem_max=2097152" >> /etc/sysctl.conf
echo "net.core.wmem_max=2097152" >> /etc/sysctl.conf
sysctl -p

# Increase the timeout for heartbeats
export RAY_timeout_ms=10000
export RAY_ping_interval_ms=1000
export RAY_heartbeat_timeout_milliseconds=10000

# Start Ray worker with explicit connection options and adjust parameters for stability
ray start --address='$HEAD_NODE_IP:6379' \\
    --metrics-export-port=8266 \\
    --num-cpus=\$(($(($(nproc) - 1)) > 0 ? $(($(nproc) - 1)) : 1)) \\
    --resources='{"worker_node": 1.0}' \\
    --system-config='{
        "raylet_heartbeat_timeout_milliseconds": 10000,
        "ping_gcs_rpc_server_max_retries": 10,
        "object_manager_pull_timeout_ms": 10000,
        "object_manager_push_timeout_ms": 10000,
        "health_check_initial_delay_ms": 10000,
        "health_check_period_ms": 5000,
        "health_check_timeout_ms": 2000,
        "num_workers_soft_limit": 10,
        "worker_max_reconnections": 100
    }' \\
    --block

echo "Ray worker node started and connected to $HEAD_NODE_IP:6379"
EOF

chmod +x /home/$USERNAME/ray-cluster/start_worker.sh
chown $USERNAME:$USERNAME /home/$USERNAME/ray-cluster/start_worker.sh

# Create script to stop Ray
cat > /home/$USERNAME/ray-cluster/stop_ray.sh << EOF
#!/bin/bash
source /home/$USERNAME/ray-env/bin/activate
ray stop
echo "Ray worker node stopped"
EOF

chmod +x /home/$USERNAME/ray-cluster/stop_ray.sh
chown $USERNAME:$USERNAME /home/$USERNAME/ray-cluster/stop_ray.sh

# Start node-exporter for monitoring
echo "Setting up node-exporter for monitoring..."
cat > /home/$USERNAME/ray-cluster/start_node_exporter.sh << 'EOF'
#!/bin/bash
docker run -d \
  --name node-exporter \
  --restart unless-stopped \
  --net="host" \
  --pid="host" \
  -v "/:/host:ro,rslave" \
  prom/node-exporter:latest \
  --path.rootfs=/host
echo "Node Exporter started for Prometheus metrics collection"
EOF

chmod +x /home/$USERNAME/ray-cluster/start_node_exporter.sh
chown $USERNAME:$USERNAME /home/$USERNAME/ray-cluster/start_node_exporter.sh

# Create script to stop node-exporter
cat > /home/$USERNAME/ray-cluster/stop_node_exporter.sh << 'EOF'
#!/bin/bash
docker stop node-exporter
docker rm node-exporter
echo "Node Exporter stopped"
EOF

chmod +x /home/$USERNAME/ray-cluster/stop_node_exporter.sh
chown $USERNAME:$USERNAME /home/$USERNAME/ray-cluster/stop_node_exporter.sh

# Create a watchdog script to monitor the Ray connection
echo "Creating Ray connection watchdog script..."
cat > /home/$USERNAME/ray-cluster/ray_watchdog.sh << 'EOF'
#!/bin/bash

# Configuration
LOG_FILE="/home/$USER/ray-cluster/watchdog.log"
MAX_RECONNECT_ATTEMPTS=3
RECONNECT_DELAY=30

# Function to log messages
log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

# Initialize log
log "Ray watchdog started"

# Get the head node IP from the start_worker.sh script
HEAD_NODE_IP=$(grep -oP "(?<=--address=')[^:']+" /home/$USER/ray-cluster/start_worker.sh)
if [ -z "$HEAD_NODE_IP" ]; then
  log "ERROR: Could not determine head node IP"
  exit 1
fi
log "Head node IP: $HEAD_NODE_IP"

while true; do
  # Check if Ray is running
  if ! pgrep -f "ray::raylet" > /dev/null; then
    log "Ray process is not running. Attempting to restart service..."
    sudo systemctl restart ray-worker
    sleep 10
    continue
  fi
  
  # Check if worker can connect to head node
  if ! ping -c 1 -W 2 "$HEAD_NODE_IP" > /dev/null 2>&1; then
    log "WARNING: Cannot ping head node at $HEAD_NODE_IP"
    # Wait and try again before taking action
    sleep 5
    if ! ping -c 1 -W 2 "$HEAD_NODE_IP" > /dev/null 2>&1; then
      log "ERROR: Still cannot ping head node. Restarting Ray service..."
      sudo systemctl restart ray-worker
    fi
  fi
  
  # Check if Ray port is accessible
  if ! nc -z -w 2 "$HEAD_NODE_IP" 6379 > /dev/null 2>&1; then
    log "WARNING: Cannot connect to Ray port on head node"
    # Wait and try again before taking action
    sleep 5
    if ! nc -z -w 2 "$HEAD_NODE_IP" 6379 > /dev/null 2>&1; then
      log "ERROR: Still cannot connect to Ray port. Restarting Ray service..."
      sudo systemctl restart ray-worker
    fi
  fi
  
  # Sleep before next check
  sleep 60
done
EOF

chmod +x /home/$USERNAME/ray-cluster/ray_watchdog.sh
chown $USERNAME:$USERNAME /home/$USERNAME/ray-cluster/ray_watchdog.sh

# Create a systemd service for the watchdog
echo "Creating systemd service for Ray watchdog..."
cat > /etc/systemd/system/ray-watchdog.service << EOF
[Unit]
Description=Ray Connection Watchdog
After=ray-worker.service
Requires=ray-worker.service

[Service]
Type=simple
User=$USERNAME
WorkingDirectory=/home/$USERNAME
ExecStart=/bin/bash /home/$USERNAME/ray-cluster/ray_watchdog.sh
Restart=always
RestartSec=60

[Install]
WantedBy=multi-user.target
EOF

# Create a systemd service to start Ray worker on boot
echo "Creating systemd service for Ray worker..."
cat > /etc/systemd/system/ray-worker.service << EOF
[Unit]
Description=Ray Worker Node
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
User=$USERNAME
WorkingDirectory=/home/$USERNAME
ExecStartPre=/bin/bash -c "rm -rf /tmp/ray"
ExecStart=/bin/bash /home/$USERNAME/ray-cluster/start_worker.sh
ExecStop=/home/$USERNAME/ray-env/bin/ray stop
Restart=always
RestartSec=20
LimitNOFILE=65536
TimeoutStartSec=180
TimeoutStopSec=60
StandardOutput=journal
StandardError=journal
# Environment variables for stability
Environment="RAY_timeout_ms=10000"
Environment="RAY_ping_interval_ms=1000" 
Environment="RAY_heartbeat_timeout_milliseconds=10000"

[Install]
WantedBy=multi-user.target
EOF

# Create a systemd service for node-exporter
echo "Creating systemd service for node-exporter..."
cat > /etc/systemd/system/node-exporter.service << EOF
[Unit]
Description=Prometheus Node Exporter
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
User=$USERNAME
WorkingDirectory=/home/$USERNAME
ExecStart=/bin/bash /home/$USERNAME/ray-cluster/start_node_exporter.sh
ExecStop=/bin/bash /home/$USERNAME/ray-cluster/stop_node_exporter.sh

[Install]
WantedBy=multi-user.target
EOF

# Set up authorized keys for passwordless SSH if requested
echo "========================================================"
echo "To enable passwordless SSH from head node, paste the head node's"
echo "public key when prompted (or press Enter to skip this step):"
echo "========================================================"
read -p "Head node's public key (or press Enter to skip): " PUBLIC_KEY

if [ ! -z "$PUBLIC_KEY" ]; then
  mkdir -p /home/$USERNAME/.ssh
  echo "$PUBLIC_KEY" >> /home/$USERNAME/.ssh/authorized_keys
  chmod 700 /home/$USERNAME/.ssh
  chmod 600 /home/$USERNAME/.ssh/authorized_keys
  chown -R $USERNAME:$USERNAME /home/$USERNAME/.ssh
  echo "Public key added to authorized_keys."
fi

# Enable and start the services
systemctl daemon-reload
systemctl enable ray-worker.service
systemctl start ray-worker.service
systemctl enable node-exporter.service
systemctl start node-exporter.service
systemctl enable ray-watchdog.service
systemctl start ray-watchdog.service

echo "========================================================"
echo "Ray worker node setup completed successfully!"
echo "========================================================"
echo ""
echo "Status:"
echo "- Ray worker service: $(systemctl is-active ray-worker.service)"
echo "- Node exporter: $(systemctl is-active node-exporter.service)"
echo ""
echo "Next steps:"
echo "1. Verify worker node status: sudo systemctl status ray-worker"
echo "2. Check if worker appears in Ray dashboard: http://$HEAD_NODE_IP:8265"
echo "3. Verify node metrics are available: http://$HEAD_NODE_IP:9090/targets"
echo ""
echo "For manual operation:"
echo "- Start worker: ~/ray-cluster/start_worker.sh"
echo "- Stop worker:  ~/ray-cluster/stop_ray.sh"
echo "- Start node-exporter: ~/ray-cluster/start_node_exporter.sh"
echo "- Stop node-exporter: ~/ray-cluster/stop_node_exporter.sh"
echo "========================================================"