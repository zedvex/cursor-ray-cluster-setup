#!/bin/bash
# Simple Ray worker setup script

set -e  # Exit on any error
set -x  # Print commands for debugging

# Log setup
LOGFILE="/tmp/ray-worker-setup.log"
exec > >(tee -a "$LOGFILE") 2>&1

echo "========== Starting simplified Ray worker setup =========="
echo "Started at $(date)"
echo "Head node: $1"

# Check if head node IP is provided
if [ -z "$1" ]; then
  echo "Error: Head node IP address not provided"
  echo "Usage: $0 <head-node-ip>"
  echo "Example: $0 192.168.1.10"
  exit 1
fi

HEAD_NODE_IP="$1"

# Check if the script is run as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root or with sudo"
    exit 1
fi

# Determine the actual user (even when run with sudo)
if [ -n "$SUDO_USER" ]; then
    ACTUAL_USER=$SUDO_USER
else
    ACTUAL_USER=$(whoami)
fi

echo "Setting up Ray worker node for user: $ACTUAL_USER"
HOME_DIR=$(eval echo ~$ACTUAL_USER)
echo "Home directory: $HOME_DIR"

# Check if user is root (not recommended but allowed)
if [ "$ACTUAL_USER" = "root" ]; then
    echo "WARNING: Setting up using root user is not recommended for production"
    echo "Consider creating a regular user first"
fi

# Create directories
mkdir -p $HOME_DIR/ray-cluster
CLUSTER_DIR="$HOME_DIR/ray-cluster"
ENV_DIR="$HOME_DIR/ray-env"

# Install dependencies
apt-get update
apt-get install -y python3 python3-pip python3-venv sudo curl

# Create virtual environment
python3 -m venv $ENV_DIR
chown -R $ACTUAL_USER:$ACTUAL_USER $ENV_DIR
chown -R $ACTUAL_USER:$ACTUAL_USER $CLUSTER_DIR

# Upgrade pip and install Ray
sudo -u $ACTUAL_USER $ENV_DIR/bin/pip install --upgrade pip

# Install a specific version of Ray that is available
sudo -u $ACTUAL_USER $ENV_DIR/bin/pip install 'ray[default]>=2.31.0' pandas numpy psutil prometheus-client

# Verify Ray installation
echo "Verifying Ray installation..."
RAY_PATH=$(sudo -u $ACTUAL_USER which ray || echo "NOT_FOUND")
if [ "$RAY_PATH" = "NOT_FOUND" ]; then
    RAY_PATH=$(find $ENV_DIR -name ray -type f -executable | head -1)
    if [ -z "$RAY_PATH" ]; then
        echo "ERROR: Ray executable not found! Installation may have failed."
        echo "Checking pip list for Ray package:"
        sudo -u $ACTUAL_USER $ENV_DIR/bin/pip list | grep -i ray
        echo "Trying to locate manually:"
        find $ENV_DIR -name "*ray*" -type d | sort
        exit 1
    fi
    echo "Found Ray at: $RAY_PATH"
else
    echo "Ray executable found at: $RAY_PATH"
fi

# Verify Ray version
echo "Ray version:"
sudo -u $ACTUAL_USER $ENV_DIR/bin/ray --version

# Configure system limits for Ray
cat > /etc/security/limits.d/ray.conf << EOF
$ACTUAL_USER soft nofile 65536
$ACTUAL_USER hard nofile 65536
EOF

# Handle /tmp/ray directory permissions
if [ -d "/tmp/ray" ]; then
    OWNER=$(stat -c '%U' /tmp/ray)
    if [ "$OWNER" != "$ACTUAL_USER" ]; then
        echo "Removing /tmp/ray directory owned by $OWNER"
        rm -rf /tmp/ray
    fi
fi

# Create fresh /tmp/ray directory with correct permissions
mkdir -p /tmp/ray
chmod 1777 /tmp/ray
chown $ACTUAL_USER:$ACTUAL_USER /tmp/ray

# Create worker start script
cat > $CLUSTER_DIR/start_worker.sh << EOF
#!/bin/bash
set -x  # Print commands for debugging

# Verify working directory
echo "Current directory: \$(pwd)"
echo "User: \$(whoami)"

# Make sure the Ray directory exists with proper permissions
if [ ! -d "/tmp/ray" ]; then
    mkdir -p /tmp/ray
    chmod 1777 /tmp/ray
fi

# Set PATH to include Ray executable
export PATH="$ENV_DIR/bin:\$PATH"
RAY_PATH="$ENV_DIR/bin/ray"

# Verify Ray is available
echo "Checking Ray executable:"
ls -la \$RAY_PATH
\$RAY_PATH --version

# Start the Ray worker
echo "Starting Ray worker node to connect to $HEAD_NODE_IP:6379"
\$RAY_PATH start --address='$HEAD_NODE_IP:6379' \\
    --num-cpus=4 \\
    --dashboard-agent-listen-port=0 \\
    --resources='{"worker_node": 1.0}' \\
    --block
EOF

chmod +x $CLUSTER_DIR/start_worker.sh
chown $ACTUAL_USER:$ACTUAL_USER $CLUSTER_DIR/start_worker.sh

# Test the worker script without blocking
echo "Testing the worker script (non-blocking)..."
TEST_CMD="$CLUSTER_DIR/start_worker.sh"
TEST_CMD=${TEST_CMD/--block/--block-for-10-seconds}
sudo -u $ACTUAL_USER bash -c "cd $CLUSTER_DIR && $TEST_CMD" || echo "Test exited with $?"

# Create systemd service for Ray worker
cat > /etc/systemd/system/ray-worker.service << EOF
[Unit]
Description=Ray Worker Node
After=network.target
StartLimitIntervalSec=500
StartLimitBurst=5

[Service]
Type=simple
User=$ACTUAL_USER
Group=$ACTUAL_USER
WorkingDirectory=$CLUSTER_DIR
Environment="PATH=$ENV_DIR/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStartPre=-/bin/sh -c "if [ ! -d /tmp/ray ]; then mkdir -p /tmp/ray && chmod 1777 /tmp/ray && chown $ACTUAL_USER:$ACTUAL_USER /tmp/ray; fi"
ExecStart=$CLUSTER_DIR/start_worker.sh
ExecStop=/bin/kill -TERM \$MAINPID
Restart=on-failure
RestartSec=30
KillMode=mixed
KillSignal=SIGTERM
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
EOF

# Create node exporter service if you want to monitor the machine
cat > /etc/systemd/system/node-exporter.service << EOF
[Unit]
Description=Node Exporter
After=network.target

[Service]
Type=simple
User=$ACTUAL_USER
Group=$ACTUAL_USER
WorkingDirectory=$CLUSTER_DIR
ExecStart=$ENV_DIR/bin/python3 -m prometheus_client.exposition --port 9100

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd configuration
systemctl daemon-reload

# Enable and start services
systemctl enable ray-worker.service
systemctl start ray-worker.service
systemctl enable node-exporter.service
systemctl start node-exporter.service

# Disable watchdog for now to help troubleshoot connection issues
# systemctl enable ray-watchdog.service
# systemctl start ray-watchdog.service
echo "Note: Watchdog will not be enabled/started for now to help troubleshoot"

# Print service status
echo "Ray Worker service status:"
systemctl status ray-worker.service

echo "Node Exporter service status:"
systemctl status node-exporter.service

echo "-------------------------------------------"
echo "Worker node setup complete!"
echo "-------------------------------------------"
echo "Worker node should now be connected to the Ray cluster at $HEAD_NODE_IP:6379"
echo "You can verify by checking the Ray dashboard at http://$HEAD_NODE_IP:8265"
echo ""
echo "Worker metrics available at: http://$(hostname):9100/metrics"
echo ""
echo "For manual operation, you can use:"
echo "$ sudo systemctl stop ray-worker # To stop the worker"
echo "$ sudo systemctl start ray-worker # To start the worker"
echo "$ sudo systemctl status ray-worker # To check the worker status"
echo ""
echo "$ sudo systemctl stop node-exporter # To stop the metrics"
echo "$ sudo systemctl start node-exporter # To start the metrics"
echo "$ sudo systemctl status node-exporter # To check the metrics status"
echo ""
echo "To check service logs:"
echo "$ sudo journalctl -u ray-worker -f # To follow worker logs"
echo "$ sudo journalctl -u node-exporter -f # To follow metrics logs"

echo "========== Ray worker setup complete =========="
echo "To check status: sudo systemctl status ray-worker"
echo "To view logs: journalctl -u ray-worker -f"
echo "To manually start worker: $CLUSTER_DIR/start_worker.sh"
echo "Completed at $(date)"