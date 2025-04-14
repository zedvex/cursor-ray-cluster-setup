#!/bin/bash
# Simple Ray head node setup script

# Log setup
LOGFILE="/tmp/ray-head-setup.log"
exec > >(tee -a "$LOGFILE") 2>&1

echo "========== Starting simplified Ray head node setup =========="
echo "Started at $(date)"

USERNAME=$(whoami)

# Core system setup
echo "Installing system dependencies..."
sudo apt-get update
sudo apt-get install -y python3-pip python3-venv

# Create virtual environment if it doesn't exist
if [ ! -d "/home/$USERNAME/ray-env" ]; then
  echo "Creating Python virtual environment..."
  python3 -m venv /home/$USERNAME/ray-env
fi

# Activate virtual environment
source /home/$USERNAME/ray-env/bin/activate

# Install Ray with minimal dependencies
echo "Installing Ray and dependencies..."
pip install --upgrade pip
pip install "ray[default]==2.10.0" pandas numpy psutil

# Ensure venv directory is accessible
chmod -R 755 /home/$USERNAME/ray-env

# Increase system limits
echo "Configuring system limits..."
echo "fs.file-max = 65536" | sudo tee -a /etc/sysctl.conf
echo "* soft nofile 65536" | sudo tee -a /etc/security/limits.conf
echo "* hard nofile 65536" | sudo tee -a /etc/security/limits.conf
sudo sysctl -p

# Create ray head start script
echo "Creating Ray head node start script..."
mkdir -p /home/$USERNAME/ray-cluster

RAY_PATH="/home/$USERNAME/ray-env/bin/ray"

cat > /home/$USERNAME/ray-cluster/start_head.sh << EOF
#!/bin/bash
# Ray head start script

# Use full path to ray executable instead of relying on environment activation
RAY_EXECUTABLE="/home/$USERNAME/ray-env/bin/ray"

# Clean up Ray directory
rm -rf /tmp/ray
mkdir -p /tmp/ray
chmod 777 /tmp/ray

# Start Ray head node with full path to executable
\$RAY_EXECUTABLE start --head \\
    --port=6379 \\
    --dashboard-host=0.0.0.0 \\
    --num-cpus=4 \\
    --dashboard-port=8265 \\
    --block
EOF

chmod +x /home/$USERNAME/ray-cluster/start_head.sh

# Create systemd service for Ray head
echo "Creating systemd service..."
sudo tee /etc/systemd/system/ray-head.service > /dev/null << EOF
[Unit]
Description=Ray Head Node
After=network.target
StartLimitIntervalSec=500
StartLimitBurst=5

[Service]
Type=simple
User=$USERNAME
WorkingDirectory=/home/$USERNAME/ray-cluster
Environment="PATH=/home/$USERNAME/ray-env/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment="PYTHONPATH=/home/$USERNAME/ray-env/lib/python3.8/site-packages"
ExecStartPre=/bin/rm -rf /tmp/ray
ExecStart=/home/$USERNAME/ray-cluster/start_head.sh
Restart=on-failure
RestartSec=30
KillMode=mixed
KillSignal=SIGTERM
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
EOF

# Configure firewall for Ray
echo "Configuring firewall..."
sudo apt-get install -y ufw
sudo ufw allow 22/tcp  # SSH
sudo ufw allow 6379/tcp  # Ray client server primary port
sudo ufw allow 8265/tcp  # Ray dashboard
sudo ufw allow 10001:11000/tcp  # Ray worker ports
sudo ufw --force enable
sudo ufw status

# Enable and start the Ray head service
echo "Enabling and starting ray-head service..."
sudo systemctl daemon-reload
sudo systemctl enable ray-head.service
sudo systemctl start ray-head.service

echo "Waiting for service to start..."
sleep 5
sudo systemctl status ray-head.service || true

# Double check that Ray is working properly
echo "Verifying Ray installation..."
$RAY_PATH --version

echo "========== Ray head node setup complete =========="
echo "To check status: sudo systemctl status ray-head"
echo "To view logs: journalctl -u ray-head -f"
echo "To manually start head node: ~/ray-cluster/start_head.sh"
echo "Dashboard available at http://$(hostname -I | awk '{print $1}'):8265"
echo "Completed at $(date)"