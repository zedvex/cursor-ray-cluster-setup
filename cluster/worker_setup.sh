#!/bin/bash
# Simple Ray worker setup script

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
  exit 1
fi

HEAD_NODE_IP="$1"

# Determine the actual username and home directory
if [ "$USER" = "root" ] && [ -n "$SUDO_USER" ]; then
  # Running via sudo
  ACTUAL_USER="$SUDO_USER"
else
  # Running as the actual user or directly as root
  ACTUAL_USER="$USER"
fi

# If we're running as root directly (not via sudo), use a regular user if available
if [ "$ACTUAL_USER" = "root" ]; then
  # Check if we have a non-root user available
  POSSIBLE_USER=$(getent passwd 1000 | cut -d: -f1)
  if [ -n "$POSSIBLE_USER" ] && [ "$POSSIBLE_USER" != "root" ]; then
    ACTUAL_USER="$POSSIBLE_USER"
    echo "Running as root, will set up Ray for user: $ACTUAL_USER"
  else
    echo "Running as root and no regular user found. Using root user (not recommended)."
  fi
fi

# Get the correct home directory
USER_HOME=$(getent passwd "$ACTUAL_USER" | cut -d: -f6)
echo "Setting up Ray for user: $ACTUAL_USER"
echo "Home directory: $USER_HOME"

# Core system setup
echo "Installing system dependencies..."
apt-get update
apt-get install -y python3-pip python3-venv

# Create virtual environment if it doesn't exist
VENV_PATH="$USER_HOME/ray-env"
if [ ! -d "$VENV_PATH" ]; then
  echo "Creating Python virtual environment at $VENV_PATH..."
  if [ "$USER" = "root" ]; then
    su - "$ACTUAL_USER" -c "python3 -m venv $VENV_PATH"
  else
    python3 -m venv "$VENV_PATH"
  fi
fi

# Install Ray with minimal dependencies
echo "Installing Ray and dependencies..."
if [ "$USER" = "root" ]; then
  su - "$ACTUAL_USER" -c "source $VENV_PATH/bin/activate && pip install --upgrade pip"
  su - "$ACTUAL_USER" -c "source $VENV_PATH/bin/activate && pip install 'ray[default]==2.10.0' pandas numpy psutil"
else
  source "$VENV_PATH/bin/activate"
  pip install --upgrade pip
  pip install "ray[default]==2.10.0" pandas numpy psutil
fi

# Ensure venv directory is accessible
chmod -R 755 "$VENV_PATH"

# Increase system limits
echo "Configuring system limits..."
echo "fs.file-max = 65536" | tee -a /etc/sysctl.conf
echo "* soft nofile 65536" | tee -a /etc/security/limits.conf
echo "* hard nofile 65536" | tee -a /etc/security/limits.conf
sysctl -p || true

# Create ray worker start script
echo "Creating Ray worker start script..."
CLUSTER_DIR="$USER_HOME/ray-cluster"
mkdir -p "$CLUSTER_DIR"

RAY_EXECUTABLE="$VENV_PATH/bin/ray"

cat > "$CLUSTER_DIR/start_worker.sh" << EOF
#!/bin/bash
# Ray worker start script

# Use full path to ray executable instead of relying on environment activation
RAY_EXECUTABLE="$RAY_EXECUTABLE"

# Clean up Ray directory
rm -rf /tmp/ray
mkdir -p /tmp/ray
chmod 777 /tmp/ray

# Start Ray worker with full path to executable
"\$RAY_EXECUTABLE" start --address='$HEAD_NODE_IP:6379' \\
    --num-cpus=4 \\
    --resources='{"worker_node": 1.0}' \\
    --dashboard-agent-listen-port=0 \\
    --block
EOF

chmod +x "$CLUSTER_DIR/start_worker.sh"
chown "$ACTUAL_USER:$ACTUAL_USER" "$CLUSTER_DIR/start_worker.sh"

# Create systemd service for Ray worker
echo "Creating systemd service..."
tee /etc/systemd/system/ray-worker.service > /dev/null << EOF
[Unit]
Description=Ray Worker Node
After=network.target
StartLimitIntervalSec=500
StartLimitBurst=5

[Service]
Type=simple
User=$ACTUAL_USER
WorkingDirectory=$CLUSTER_DIR
Environment="PATH=$VENV_PATH/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment="PYTHONPATH=$VENV_PATH/lib/python3.8/site-packages"
ExecStartPre=/bin/bash -c 'rm -rf /tmp/ray && mkdir -p /tmp/ray && chmod 777 /tmp/ray'
ExecStart=$CLUSTER_DIR/start_worker.sh
Restart=on-failure
RestartSec=30
KillMode=mixed
KillSignal=SIGTERM
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
EOF

# Enable and start the Ray worker service
echo "Enabling and starting ray-worker service..."
systemctl daemon-reload
systemctl enable ray-worker.service
systemctl stop ray-worker.service || true
sleep 2
systemctl start ray-worker.service

echo "Waiting for service to start..."
sleep 5
systemctl status ray-worker.service || true

# Double check that Ray is working properly
echo "Verifying Ray installation..."
if [ "$USER" = "root" ]; then
  su - "$ACTUAL_USER" -c "$RAY_EXECUTABLE --version"
else
  "$RAY_EXECUTABLE" --version
fi

echo "========== Ray worker setup complete =========="
echo "To check status: sudo systemctl status ray-worker"
echo "To view logs: journalctl -u ray-worker -f"
echo "To manually start worker: $CLUSTER_DIR/start_worker.sh"
echo "Completed at $(date)"