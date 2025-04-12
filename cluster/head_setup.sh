#!/bin/bash
# Head Node Setup Script for Ray Cluster (NUC)
# Run this on your Ubuntu Desktop NUC to configure it as the Ray head node

set -e  # Exit immediately if a command exits with a non-zero status

echo "========================================================"
echo "Setting up Ray Cluster Head Node on Ubuntu Desktop (NUC)"
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
  nfs-kernel-server \
  git \
  build-essential \
  libssl-dev \
  openssh-server \
  curl \
  wget

# Create directories
echo "Creating required directories..."
mkdir -p /mnt/code
mkdir -p /home/$USERNAME/ray-cluster
chown -R $USERNAME:$USERNAME /mnt/code
chown -R $USERNAME:$USERNAME /home/$USERNAME/ray-cluster

# Set up NFS exports
echo "Configuring NFS exports..."
if ! grep -q "/mnt/code" /etc/exports; then
  echo "/mnt/code *(rw,sync,no_subtree_check,no_root_squash)" >> /etc/exports
  exportfs -a
  systemctl restart nfs-kernel-server
  echo "NFS server configured."
else
  echo "NFS export already configured."
fi

# Set up Python virtual environment
echo "Setting up Python virtual environment..."
su - $USERNAME -c "python3 -m venv /home/$USERNAME/ray-env"
su - $USERNAME -c "echo 'source ~/ray-env/bin/activate' >> /home/$USERNAME/.bashrc"

# Install Ray and dependencies in the virtual environment
echo "Installing Ray and dependencies..."
su - $USERNAME -c "source /home/$USERNAME/ray-env/bin/activate && pip install --upgrade pip"
su - $USERNAME -c "source /home/$USERNAME/ray-env/bin/activate && pip install 'ray[default]' pandas numpy matplotlib scikit-learn fastapi uvicorn requests psutil"

# Optional: Install Anthropic if you'll be using Claude API
su - $USERNAME -c "source /home/$USERNAME/ray-env/bin/activate && pip install anthropic python-dotenv"

# Configure firewall to allow Ray
echo "Configuring firewall..."
ufw allow 22/tcp
ufw allow 6379/tcp      # Ray client server
ufw allow 8265/tcp      # Ray dashboard
ufw allow 8000/tcp      # For FastAPI server
ufw allow 8080/tcp      # For web dashboard
ufw allow 10001/tcp     # Ray internal communication
ufw --force enable

# Create a script to start Ray head node
echo "Creating Ray start script..."
cat > /home/$USERNAME/ray-cluster/start_head.sh << 'EOF'
#!/bin/bash
source ~/ray-env/bin/activate
ray start --head --port=6379 --dashboard-host=0.0.0.0 --include-dashboard=true --dashboard-port=8265
echo "Ray head node started. Dashboard available at http://$(hostname -I | awk '{print $1}'):8265"
EOF

chmod +x /home/$USERNAME/ray-cluster/start_head.sh
chown $USERNAME:$USERNAME /home/$USERNAME/ray-cluster/start_head.sh

# Create script to stop Ray
cat > /home/$USERNAME/ray-cluster/stop_ray.sh << 'EOF'
#!/bin/bash
source ~/ray-env/bin/activate
ray stop
echo "Ray stopped"
EOF

chmod +x /home/$USERNAME/ray-cluster/stop_ray.sh
chown $USERNAME:$USERNAME /home/$USERNAME/ray-cluster/stop_ray.sh

# Create .env template for Claude API
cat > /home/$USERNAME/ray-cluster/.env.example << 'EOF'
# API Keys
ANTHROPIC_API_KEY=your_anthropic_api_key_here
CLAUDE_MODEL=claude-3-7-sonnet-20250219

# Cluster Configuration
RAY_HEAD_IP=$(hostname -I | awk '{print $1}')
RAY_DASHBOARD_PORT=8265
API_SERVER_PORT=8000
DASHBOARD_PORT=8080
EOF

chown $USERNAME:$USERNAME /home/$USERNAME/ray-cluster/.env.example

# Generate SSH key for the user if it doesn't exist
if [ ! -f "/home/$USERNAME/.ssh/id_rsa" ]; then
  echo "Generating SSH key for passwordless authentication between nodes..."
  su - $USERNAME -c "ssh-keygen -t rsa -N '' -f /home/$USERNAME/.ssh/id_rsa"
  echo "SSH key generated."
  echo "IMPORTANT: You'll need to manually copy this public key to worker nodes:"
  echo "Public key: $(cat /home/$USERNAME/.ssh/id_rsa.pub)"
fi

echo "========================================================"
echo "Ray head node setup completed successfully!"
echo "========================================================"
echo ""
echo "Next steps:"
echo "1. Start Ray head node:     ~/ray-cluster/start_head.sh"
echo "2. Install worker nodes:    Copy and run worker_setup.sh on each i5 machine"
echo "3. Customize environment:   Copy .env.example to .env and edit as needed"
echo ""
echo "Ray dashboard will be available at: http://$(hostname -I | awk '{print $1}'):8265"
echo "========================================================"