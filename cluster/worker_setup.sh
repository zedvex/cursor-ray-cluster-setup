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
  lsb-release

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

# Install Ray
echo "Installing Ray..."
pip3 install --upgrade pip
pip3 install 'ray[default]' pandas numpy psutil prometheus-client

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
ray start --address='$HEAD_NODE_IP:6379' --metrics-export-port=8266
echo "Ray worker node started and connected to $HEAD_NODE_IP:6379"
EOF

chmod +x /home/$USERNAME/ray-cluster/start_worker.sh
chown $USERNAME:$USERNAME /home/$USERNAME/ray-cluster/start_worker.sh

# Create script to stop Ray
cat > /home/$USERNAME/ray-cluster/stop_ray.sh << 'EOF'
#!/bin/bash
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

# Create a systemd service to start Ray worker on boot
echo "Creating systemd service for Ray worker..."
cat > /etc/systemd/system/ray-worker.service << EOF
[Unit]
Description=Ray Worker Node
After=network.target

[Service]
Type=simple
User=$USERNAME
WorkingDirectory=/home/$USERNAME
ExecStart=/bin/bash /home/$USERNAME/ray-cluster/start_worker.sh
Restart=on-failure
RestartSec=5

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

# Enable and start the services
systemctl daemon-reload
systemctl enable ray-worker.service
systemctl start ray-worker.service
systemctl enable node-exporter.service
systemctl start node-exporter.service

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