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

# Increase system limits
echo "Configuring system limits..."
echo "fs.file-max = 65536" | sudo tee -a /etc/sysctl.conf
echo "* soft nofile 65536" | sudo tee -a /etc/security/limits.conf
echo "* hard nofile 65536" | sudo tee -a /etc/security/limits.conf
sudo sysctl -p

# Create ray worker start script
echo "Creating Ray worker start script..."
mkdir -p /home/$USERNAME/ray-cluster

cat > /home/$USERNAME/ray-cluster/start_worker.sh << EOF
#!/bin/bash
# Ray worker start script

source /home/$USERNAME/ray-env/bin/activate

# Clean up Ray directory
rm -rf /tmp/ray
mkdir -p /tmp/ray
chmod 777 /tmp/ray

# Start Ray worker
ray start --address='$HEAD_NODE_IP:6379' \\
    --num-cpus=4 \\
    --resources='{"worker_node": 1.0}' \\
    --dashboard-agent-listen-port=0 \\
    --block
EOF

chmod +x /home/$USERNAME/ray-cluster/start_worker.sh

# Create systemd service for Ray worker
echo "Creating systemd service..."
sudo tee /etc/systemd/system/ray-worker.service > /dev/null << EOF
[Unit]
Description=Ray Worker Node
After=network.target
StartLimitIntervalSec=500
StartLimitBurst=5

[Service]
Type=simple
User=$USERNAME
WorkingDirectory=/home/$USERNAME/ray-cluster
ExecStartPre=/bin/rm -rf /tmp/ray
ExecStart=/home/$USERNAME/ray-cluster/start_worker.sh
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
sudo systemctl daemon-reload
sudo systemctl enable ray-worker.service
sudo systemctl start ray-worker.service

echo "Waiting for service to start..."
sleep 5
sudo systemctl status ray-worker.service

echo "========== Ray worker setup complete =========="
echo "To check status: sudo systemctl status ray-worker"
echo "To view logs: journalctl -u ray-worker -f"
echo "To manually start worker: ~/ray-cluster/start_worker.sh"
echo "Completed at $(date)"