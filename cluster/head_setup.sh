#!/bin/bash
# Simple Ray head node setup script

# Log setup
LOGFILE="/tmp/ray-head-setup.log"
exec > >(tee -a "$LOGFILE") 2>&1

echo "========== Starting simplified Ray head node setup =========="
echo "Started at $(date)"

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

# Fix /tmp directory permissions and remove any existing Ray directory
echo "Fixing /tmp directory permissions..."
# Check if /tmp/ray exists and who owns it
if [ -d "/tmp/ray" ]; then
  echo "Found existing /tmp/ray directory"
  TMP_OWNER=$(stat -c '%U' /tmp/ray)
  echo "Current owner: $TMP_OWNER"
  
  # Try to cleanly remove the directory
  if [ "$TMP_OWNER" != "$ACTUAL_USER" ]; then
    echo "Removing /tmp/ray with sudo..."
    rm -rf /tmp/ray
  fi
fi

# Create a fresh /tmp/ray with proper permissions
echo "Creating fresh /tmp/ray directory with proper permissions..."
mkdir -p /tmp/ray
chmod 1777 /tmp/ray
chown $ACTUAL_USER:$ACTUAL_USER /tmp/ray

# Create ray head start script
echo "Creating Ray head node start script..."
CLUSTER_DIR="$USER_HOME/ray-cluster"
mkdir -p "$CLUSTER_DIR"

RAY_EXECUTABLE="$VENV_PATH/bin/ray"

cat > "$CLUSTER_DIR/start_head.sh" << EOF
#!/bin/bash
# Ray head start script

# Use full path to ray executable instead of relying on environment activation
RAY_EXECUTABLE="$RAY_EXECUTABLE"

# Don't try to remove /tmp/ray, just ensure it exists with proper permissions
if [ ! -d "/tmp/ray" ]; then
  mkdir -p /tmp/ray
  chmod 777 /tmp/ray
fi

# Start Ray head node with full path to executable
"\$RAY_EXECUTABLE" start --head \\
    --port=6379 \\
    --dashboard-host=0.0.0.0 \\
    --num-cpus=4 \\
    --dashboard-port=8265 \\
    --block
EOF

chmod +x "$CLUSTER_DIR/start_head.sh"
chown "$ACTUAL_USER:$ACTUAL_USER" "$CLUSTER_DIR/start_head.sh"

# Create systemd service for Ray head
echo "Creating systemd service..."
tee /etc/systemd/system/ray-head.service > /dev/null << EOF
[Unit]
Description=Ray Head Node
After=network.target
StartLimitIntervalSec=500
StartLimitBurst=5

[Service]
Type=simple
User=$ACTUAL_USER
WorkingDirectory=$CLUSTER_DIR
Environment="PATH=$VENV_PATH/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment="PYTHONPATH=$VENV_PATH/lib/python3.8/site-packages"
# Don't try to remove /tmp/ray in ExecStartPre, just ensure directory exists
ExecStartPre=/bin/mkdir -p /tmp/ray
ExecStartPre=/bin/chmod 777 /tmp/ray
ExecStart=$CLUSTER_DIR/start_head.sh
Restart=on-failure
RestartSec=30
KillMode=mixed
KillSignal=SIGTERM
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
EOF

# One-time fix for potential /tmp permissions issues
echo "Ensuring /tmp has sticky bit set..."
chmod 1777 /tmp

# Configure firewall for Ray
echo "Configuring firewall..."
apt-get install -y ufw
ufw allow 22/tcp  # SSH
ufw allow 6379/tcp  # Ray client server primary port
ufw allow 8265/tcp  # Ray dashboard
ufw allow 10001:11000/tcp  # Ray worker ports
ufw --force enable
ufw status

# Enable and start the Ray head service
echo "Enabling and starting ray-head service..."
systemctl daemon-reload
systemctl enable ray-head.service
systemctl stop ray-head.service || true
sleep 2
systemctl start ray-head.service

echo "Waiting for service to start..."
sleep 5
systemctl status ray-head.service || true

# Double check that Ray is working properly
echo "Verifying Ray installation..."
if [ "$USER" = "root" ]; then
  su - "$ACTUAL_USER" -c "$RAY_EXECUTABLE --version"
else
  "$RAY_EXECUTABLE" --version
fi

# Get the current host IP for instructions
HOST_IP=$(hostname -I | awk '{print $1}')

echo "========== Ray head node setup complete =========="
echo "To check status: sudo systemctl status ray-head"
echo "To view logs: journalctl -u ray-head -f"
echo "To manually start head node: $CLUSTER_DIR/start_head.sh"
echo "Dashboard available at http://$HOST_IP:8265"
echo "Completed at $(date)"
echo ""
echo "To connect worker nodes, run on each worker:"
echo "git clone https://github.com/yourusername/cursor-ray-cluster-setup.git"
echo "cd cursor-ray-cluster-setup"
echo "sudo bash cluster/worker_setup.sh $HOST_IP"