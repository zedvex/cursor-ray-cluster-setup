#!/bin/bash
# Worker Node Setup Script for Ray Cluster (i5 machines)
# Run this on your Ubuntu Server i5 machines to configure them as Ray worker nodes

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

# Update package lists
echo "Updating package lists..."
apt-get update

# Install required packages
echo "Installing required packages..."
apt-get install -y \
  python3-pip \
  python3-venv \
  python3-dev \
  tmux \
  htop \
  nfs-common \
  git \
  build-essential \
  openssh-server \
  curl \
  wget

# Create directories
echo "Creating required directories..."
mkdir -p /mnt/code
mkdir -p /home/$USERNAME/ray-cluster
chown -R $USERNAME:$USERNAME /mnt/code
chown -R $USERNAME:$USERNAME /home/$USERNAME/ray-cluster

# Mount NFS share from head node
echo "Setting up NFS client to mount /mnt/code..."
if ! grep -q "$HEAD_NODE_IP:/mnt/code" /etc/fstab; then
  echo "$HEAD_NODE_IP:/mnt/code /mnt/code nfs defaults,nofail,x-systemd.automount 0 0" >> /etc/fstab
  echo "Added NFS mount to fstab."
else
  echo "NFS mount already in fstab."
fi

# Try mounting now
echo "Mounting NFS share..."
mount -t nfs $HEAD_NODE_IP:/mnt/code /mnt/code || echo "NFS mount failed. Will try on next reboot."

# Set up Python virtual environment
echo "Setting up Python virtual environment..."
su - $USERNAME -c "python3 -m venv /home/$USERNAME/ray-env"
su - $USERNAME -c "echo 'source ~/ray-env/bin/activate' >> /home/$USERNAME/.bashrc"

# Install Ray in the virtual environment
echo "Installing Ray..."
su - $USERNAME -c "source /home/$USERNAME/ray-env/bin/activate && pip install --upgrade pip"
su - $USERNAME -c "source /home/$USERNAME/ray-env/bin/activate && pip install 'ray[default]' pandas numpy psutil"

# Configure firewall
echo "Configuring firewall..."
ufw allow 22/tcp
ufw allow 6379/tcp      # Ray client server
ufw allow 10001/tcp     # Ray internal communication
ufw --force enable

# Create a script to start Ray worker node
echo "Creating Ray worker start script..."
cat > /home/$USERNAME/ray-cluster/start_worker.sh << EOF
#!/bin/bash
source ~/ray-env/bin/activate
ray start --address='$HEAD_NODE_IP:6379'
echo "Ray worker node started and connected to $HEAD_NODE_IP:6379"
EOF

chmod +x /home/$USERNAME/ray-cluster/start_worker.sh
chown $USERNAME:$USERNAME /home/$USERNAME/ray-cluster/start_worker.sh

# Create script to stop Ray
cat > /home/$USERNAME/ray-cluster/stop_ray.sh << 'EOF'
#!/bin/bash
source ~/ray-env/bin/activate
ray stop
echo "Ray worker node stopped"
EOF

chmod +x /home/$USERNAME/ray-cluster/stop_ray.sh
chown $USERNAME:$USERNAME /home/$USERNAME/ray-cluster/stop_ray.sh

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

# Enable and start the Ray worker service
systemctl daemon-reload
systemctl enable ray-worker.service
systemctl start ray-worker.service

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
echo "Next steps:"
echo "1. Verify worker node status: sudo systemctl status ray-worker"
echo "2. Check if worker appears in Ray dashboard: http://$HEAD_NODE_IP:8265"
echo ""
echo "For manual operation:"
echo "- Start worker: ~/ray-cluster/start_worker.sh"
echo "- Stop worker:  ~/ray-cluster/stop_ray.sh"
echo "========================================================"