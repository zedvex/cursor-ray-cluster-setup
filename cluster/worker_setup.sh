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
# Install the latest Ray version that's compatible with Python 3.12 with exact dependencies that work well together
su - $USERNAME -c "source /home/$USERNAME/ray-env/bin/activate && pip install ray[default]==2.44.1 pandas==2.1.1 numpy==1.26.1 psutil==5.9.6 prometheus-client==0.17.1"

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

# Simple logging to file
LOG_DIR="/home/$USERNAME/ray-cluster/logs"
mkdir -p \$LOG_DIR
LOG_FILE="\$LOG_DIR/ray_worker_\$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "\$LOG_FILE") 2>&1

# Basic setup
echo "[$(date)] Starting Ray worker process..."
source /home/$USERNAME/ray-env/bin/activate

# Stop any existing Ray processes
echo "[$(date)] Stopping any existing Ray processes..."
ray stop 2>/dev/null || true
sleep 2

# Make sure /tmp/ray exists and has proper permissions
echo "[$(date)] Checking Ray directories..."
mkdir -p /tmp/ray 2>/dev/null || true

# Set critical environment variables for network reliability
echo "[$(date)] Setting environment variables for network stability..."
export RAY_RAYLET_HEARTBEAT_TIMEOUT_MILLISECONDS=120000
export RAY_RAYLET_STARTUP_RETRY=10
export RAY_TIMEOUT_MS=120000
export RAY_METRICS_EXPORT_PORT=8080
export RAY_NUM_HEARTBEATS_TIMEOUT=75
export RAY_HEARTBEAT_TIMEOUT_MILLISECONDS=10000

# Simple Ray start command with matching parameters to head node
echo "[$(date)] Starting Ray worker with connection to $HEAD_NODE_IP:6379"
exec ray start --address='$HEAD_NODE_IP:6379' \
  --metrics-export-port=8081 \
  --num-cpus=4 \
  --system-config='{"health_check_failure_threshold": 30, "health_check_period_ms": 10000, "health_check_initial_delay_ms": 60000}' \
  --block
EOF

chmod +x /home/$USERNAME/ray-cluster/start_worker.sh
chown $USERNAME:$USERNAME /home/$USERNAME/ray-cluster/start_worker.sh

# Create script to stop Ray (simple version)
cat > /home/$USERNAME/ray-cluster/stop_ray.sh << EOF
#!/bin/bash
source /home/$USERNAME/ray-env/bin/activate
ray stop
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

# Create a simplified watchdog script to monitor the Ray connection
echo "Creating simple Ray connection watchdog script..."
cat > /home/$USERNAME/ray-cluster/ray_watchdog.sh << 'EOF'
#!/bin/bash

# Configuration
LOG_FILE="/home/$USER/ray-cluster/watchdog.log"

# Function to log messages
log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

# Initialize log
log "Ray simple watchdog started"

while true; do
  # Check if Ray is running
  if ! pgrep -f "ray::raylet" > /dev/null; then
    log "Ray process not detected - restarting service"
    sudo systemctl restart ray-worker
  fi
  
  # Sleep for 2 minutes before next check
  sleep 120
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
After=network-online.target docker.service
Wants=network-online.target
Conflicts=shutdown.target reboot.target halt.target
StartLimitIntervalSec=5400
StartLimitBurst=10

[Service]
Type=simple
User=$USERNAME
WorkingDirectory=/home/$USERNAME
# Create a helper script to handle ray directory setup
ExecStartPre=/bin/bash -c "source /home/$USERNAME/ray-env/bin/activate && ray stop || true"
ExecStart=/bin/bash /home/$USERNAME/ray-cluster/start_worker.sh
ExecStop=/home/$USERNAME/ray-env/bin/ray stop
KillMode=process
KillSignal=SIGTERM
SendSIGKILL=yes
TimeoutStartSec=180
TimeoutStopSec=90
Restart=always
RestartSec=90
SuccessExitStatus=0 143 137

[Install]
WantedBy=multi-user.target
EOF

# Create a helper script to prepare Ray directories with root permissions
echo "Creating Ray directory setup script..."
cat > /home/$USERNAME/ray-cluster/prepare_ray_dirs.sh << 'EOF'
#!/bin/bash
# This script is meant to be run as root (via sudo)
rm -rf /tmp/ray
mkdir -p /tmp/ray
chmod -R 777 /tmp/ray
# Get the username from the first argument
USERNAME=$1
if [ -n "$USERNAME" ]; then
  chown -R $USERNAME:$USERNAME /tmp/ray
fi
echo "Ray directories prepared successfully"
EOF

chmod +x /home/$USERNAME/ray-cluster/prepare_ray_dirs.sh
chown $USERNAME:$USERNAME /home/$USERNAME/ray-cluster/prepare_ray_dirs.sh

# Create a new log monitoring script
echo "Creating log monitoring script..."
cat > /home/$USERNAME/ray-cluster/monitor_ray_logs.sh << 'EOF'
#!/bin/bash

LOG_DIR="/home/$USERNAME/ray-cluster/logs"
LAST_CHECK_FILE="/home/$USERNAME/ray-cluster/last_log_check"
mkdir -p "$LOG_DIR"

# Initialize last check time if it doesn't exist
if [ ! -f "$LAST_CHECK_FILE" ]; then
  date +%s > "$LAST_CHECK_FILE"
fi

# Get errors and warnings from logs since last check
LAST_CHECK=$(cat "$LAST_CHECK_FILE")
CURRENT_TIME=$(date +%s)

# Record current time for next check
echo "$CURRENT_TIME" > "$LAST_CHECK_FILE"

# Find all log files
LOG_FILES=$(find "$LOG_DIR" -name "ray_worker_*.log")

if [ -z "$LOG_FILES" ]; then
  echo "No log files found in $LOG_DIR"
  exit 0
fi

# Look for important error patterns
echo "=== Ray Worker Error Report $(date) ==="

for LOG_FILE in $LOG_FILES; do
  MODIFIED_TIME=$(stat -c %Y "$LOG_FILE")
  if [ "$MODIFIED_TIME" -gt "$LAST_CHECK" ]; then
    echo "--- Analyzing log file: $LOG_FILE ---"
    
    # Check for critical error patterns
    grep -i -E "error|exception|fail|cannot|timeout|refused" "$LOG_FILE" | tail -n 50
    
    # Look for important Ray-specific messages
    grep -i -E "ray.*dead|connection.*lost|node.*dead|raylet" "$LOG_FILE" | tail -n 50
  fi
done

echo "=== End of Report ==="
EOF

chmod +x /home/$USERNAME/ray-cluster/monitor_ray_logs.sh
chown $USERNAME:$USERNAME /home/$USERNAME/ray-cluster/monitor_ray_logs.sh

# Create a cron job to run the monitoring script
echo "Setting up log monitoring cron job..."
CRON_JOB="*/10 * * * * /home/$USERNAME/ray-cluster/monitor_ray_logs.sh > /home/$USERNAME/ray-cluster/logs/monitor_$(date +\%Y\%m\%d).log 2>&1"
(crontab -u $USERNAME -l 2>/dev/null || echo "") | grep -v "monitor_ray_logs.sh" | { cat; echo "$CRON_JOB"; } | crontab -u $USERNAME -

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

# Enable and start the services
systemctl daemon-reload

# Stop all Ray-related services to ensure clean start
systemctl stop ray-worker.service ray-watchdog.service node-exporter.service 2>/dev/null || true
systemctl disable ray-watchdog.service 2>/dev/null || true

# Remove any stale Ray directories and ensure proper permissions
echo "Setting up Ray directories with proper permissions..."
sudo rm -rf /tmp/ray || true
sudo mkdir -p /tmp/ray
sudo chmod -R 777 /tmp/ray
sudo chown -R $USERNAME:$USERNAME /tmp/ray
# Also set up Ray directories for the user
sudo /home/$USERNAME/ray-cluster/prepare_ray_dirs.sh $USERNAME

# Enable and start the key services one at a time with proper checking
echo "Enabling and starting node-exporter service..."
systemctl enable node-exporter.service
systemctl restart node-exporter.service
sleep 5
if systemctl is-active --quiet node-exporter.service; then
  echo "Node exporter service started successfully."
else
  echo "WARNING: Node exporter service failed to start properly."
fi

echo "Enabling and starting Ray worker service..."
systemctl enable ray-worker.service
systemctl restart ray-worker.service
sleep 10
if systemctl is-active --quiet ray-worker.service; then
  echo "Ray worker service started successfully."
else
  echo "WARNING: Ray worker service failed to start properly. Check the logs for details."
  journalctl -u ray-worker.service -n 50 --no-pager
fi

# Set up sudo permissions for Ray directory management
echo "Setting up sudo permissions for Ray operations..."
cat > /etc/sudoers.d/ray-worker << EOF
# Allow Ray user to manage Ray directories without password
$USERNAME ALL=(ALL) NOPASSWD: /bin/rm -rf /tmp/ray, /bin/rm -rf /tmp/ray/*
$USERNAME ALL=(ALL) NOPASSWD: /bin/mkdir -p /tmp/ray
$USERNAME ALL=(ALL) NOPASSWD: /bin/chmod -R 777 /tmp/ray
$USERNAME ALL=(ALL) NOPASSWD: /bin/chown -R * /tmp/ray
EOF
chmod 440 /etc/sudoers.d/ray-worker

echo "========================================================"
echo "Ray worker node setup completed!"
echo "========================================================"
echo ""
echo "STATUS INFORMATION:"
echo "- Ray worker service: $(systemctl is-active ray-worker.service)"
echo "- Ray worker service logs:"
journalctl -u ray-worker.service -n 20 --no-pager
echo ""
echo "NEXT STEPS:"
echo "- Check if worker appears in Ray dashboard: http://$HEAD_NODE_IP:8265"
echo ""
echo "TROUBLESHOOTING COMMANDS:"
echo "- Restart worker:       sudo systemctl restart ray-worker"
echo "- Check worker status:  sudo systemctl status ray-worker"
echo "- View logs:            sudo journalctl -fu ray-worker"
echo "========================================================"