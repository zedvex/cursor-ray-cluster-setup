#!/bin/bash
# NFS Server Setup Script for Ray Cluster
# This script configures the NFS server on the head node (NUC)

set -e  # Exit immediately if a command exits with a non-zero status

# Display header
echo "========================================================"
echo "Setting up NFS Server for Ray Cluster"
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

echo "Setting up NFS server for user: $USERNAME"

# Load environment variables if .env file exists
if [ -f ../.env ]; then
    echo "Loading environment variables from .env"
    set -a
    source ../.env
    set +a
else
    echo "No .env file found, using default settings"
fi

# Default NFS export directory
NFS_EXPORT_DIR=${NFS_EXPORT_DIR:-"/mnt/code"}

# Install NFS server packages
echo "Installing NFS server packages..."
apt-get update
apt-get install -y nfs-kernel-server

# Create the export directory if it doesn't exist
echo "Creating NFS export directory: $NFS_EXPORT_DIR"
mkdir -p $NFS_EXPORT_DIR
chown -R $USERNAME:$USERNAME $NFS_EXPORT_DIR
chmod 755 $NFS_EXPORT_DIR

# Configure NFS exports
echo "Configuring NFS exports..."
if ! grep -q "$NFS_EXPORT_DIR" /etc/exports; then
    echo "$NFS_EXPORT_DIR *(rw,sync,no_subtree_check,no_root_squash)" >> /etc/exports
    echo "Added NFS export to /etc/exports"
else
    echo "NFS export already configured in /etc/exports"
fi

# Update NFS exports
exportfs -a
systemctl restart nfs-kernel-server

# Configure firewall for NFS
echo "Configuring firewall for NFS..."
if command -v ufw > /dev/null 2>&1; then
    ufw allow 2049/tcp  # NFS
    ufw allow 111/tcp   # RPC
    ufw allow 111/udp   # RPC
    ufw allow 892/tcp   # mountd
    ufw allow 892/udp   # mountd
    ufw allow 32765:32769/tcp  # NFS dynamic ports
    ufw allow 32765:32769/udp  # NFS dynamic ports
    echo "Firewall configured for NFS"
else
    echo "ufw not found, skipping firewall configuration"
fi

# Get server IP address for clients
SERVER_IP=$(hostname -I | awk '{print $1}')

echo "========================================================"
echo "NFS Server Setup Completed"
echo "========================================================"
echo "NFS export: $NFS_EXPORT_DIR"
echo "Server IP: $SERVER_IP"
echo ""
echo "To mount this NFS share on client machines, run:"
echo "mount -t nfs $SERVER_IP:$NFS_EXPORT_DIR /mnt/code"
echo ""
echo "Or add the following line to /etc/fstab on client machines:"
echo "$SERVER_IP:$NFS_EXPORT_DIR /mnt/code nfs defaults,nofail,x-systemd.automount 0 0"
echo "========================================================"

# Create a sample fstab entry for clients
cat > fstab_example.txt << EOL
# Mount NFS share from head node
$SERVER_IP:$NFS_EXPORT_DIR /mnt/code nfs defaults,nofail,x-systemd.automount 0 0
EOL

echo "Created sample fstab entry in fstab_example.txt"
