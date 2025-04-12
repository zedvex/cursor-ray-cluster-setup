#!/bin/bash
# NFS Client Setup Script for Ray Cluster
# This script configures the NFS client on worker nodes

set -e  # Exit immediately if a command exits with a non-zero status

# Check if the server IP was provided
if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <nfs-server-ip>"
    echo "Example: $0 192.168.1.100"
    exit 1
fi

SERVER_IP=$1

# Display header
echo "========================================================"
echo "Setting up NFS Client for Ray Cluster"
echo "NFS Server IP: $SERVER_IP"
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

echo "Setting up NFS client for user: $USERNAME"

# Load environment variables if .env file exists
if [ -f ../.env ]; then
    echo "Loading environment variables from .env"
    set -a
    source ../.env
    set +a
else
    echo "No .env file found, using default settings"
fi

# Default NFS mount directory
NFS_MOUNT_DIR=${NFS_MOUNT_DIR:-"/mnt/code"}
NFS_EXPORT_DIR=${NFS_EXPORT_DIR:-"/mnt/code"}

# Install NFS client packages
echo "Installing NFS client packages..."
apt-get update
apt-get install -y nfs-common

# Create the mount directory if it doesn't exist
echo "Creating NFS mount directory: $NFS_MOUNT_DIR"
mkdir -p $NFS_MOUNT_DIR
chown $USERNAME:$USERNAME $NFS_MOUNT_DIR

# Check if the server is reachable
echo "Checking if NFS server is reachable..."
if ping -c 1 $SERVER_IP &> /dev/null; then
    echo "NFS server is reachable."
else
    echo "WARNING: NFS server is not reachable. Make sure the server IP is correct and the server is running."
    echo "Continuing setup anyway..."
fi

# Check if the export is available
echo "Checking if NFS export is available..."
if showmount -e $SERVER_IP &> /dev/null; then
    echo "NFS exports from $SERVER_IP:"
    showmount -e $SERVER_IP
else
    echo "WARNING: Could not get NFS exports from server. Make sure the server is running and NFS is properly configured."
    echo "Continuing setup anyway..."
fi

# Configure fstab for automounting
echo "Configuring fstab for automounting..."
if ! grep -q "$SERVER_IP:$NFS_EXPORT_DIR" /etc/fstab; then
    echo "# Mount NFS share from Ray cluster head node" >> /etc/fstab
    echo "$SERVER_IP:$NFS_EXPORT_DIR $NFS_MOUNT_DIR nfs defaults,nofail,x-systemd.automount 0 0" >> /etc/fstab
    echo "Added NFS mount to /etc/fstab"
else
    echo "NFS mount already configured in /etc/fstab"
fi

# Try mounting the NFS share
echo "Attempting to mount NFS share..."
mount -t nfs $SERVER_IP:$NFS_EXPORT_DIR $NFS_MOUNT_DIR

# Check if mount succeeded
if mount | grep -q "$NFS_MOUNT_DIR"; then
    echo "NFS share mounted successfully."
    
    # Create a test file to verify write access
    echo "Verifying write access..."
    TEST_FILE="$NFS_MOUNT_DIR/client_test_$(hostname)_$(date +%s)"
    if touch "$TEST_FILE" &> /dev/null; then
        echo "Write access verified: created $TEST_FILE"
        rm "$TEST_FILE"
    else
        echo "WARNING: Could not write to NFS share. Check permissions."
    fi
else
    echo "WARNING: NFS share could not be mounted. It will be attempted at next boot."
    echo "You can try mounting manually with: mount -t nfs $SERVER_IP:$NFS_EXPORT_DIR $NFS_MOUNT_DIR"
fi

echo "========================================================"
echo "NFS Client Setup Completed"
echo "========================================================"
echo "NFS server: $SERVER_IP:$NFS_EXPORT_DIR"
echo "Mount point: $NFS_MOUNT_DIR"
echo ""
echo "The NFS share should mount automatically at boot."
echo "If you have issues, check the server configuration or try mounting manually."
echo "========================================================"
