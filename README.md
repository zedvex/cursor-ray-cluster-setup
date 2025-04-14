# Simplified Ray Cluster Setup

A minimalist system for distributing CPU-intensive workloads across multiple machines using Ray.

## Overview

This project creates a basic distributed computing environment that:

1. Uses a head node to coordinate Ray cluster operations
2. Distributes tasks across worker nodes
3. Provides tools for distributed code processing (linting, formatting, indexing, testing)

## System Requirements

### Head Node Requirements
- Ubuntu 20.04+ (Desktop or Server edition)
- Python 3.8+
- 8GB+ RAM recommended

### Worker Node Requirements
- Ubuntu 20.04+ (Server edition works great)
- Python 3.8+
- 4GB+ RAM
- Network connectivity to head node

## Quick Start

### 1. Set up the Head Node

```bash
# Clone this repository
git clone https://github.com/yourusername/cursor-ray-cluster-setup.git
cd cursor-ray-cluster-setup

# Run the head setup script (must run as root/sudo)
sudo bash cluster/head_setup.sh
```

### 2. Set up Worker Nodes

On each worker machine:

```bash
# Clone the repository
git clone https://github.com/yourusername/cursor-ray-cluster-setup.git
cd cursor-ray-cluster-setup

# Run the worker setup script with the head node IP (must run as root/sudo)
sudo bash cluster/worker_setup.sh 192.168.1.10
```

The setup script will handle:
- Installing Python and dependencies
- Creating a Python virtual environment
- Installing Ray 2.10.0
- Setting up a systemd service to run Ray
- Configuring system limits
- Starting Ray worker process

### 3. Verify Cluster Setup

On the head node:

```bash
# Check Ray service status
sudo systemctl status ray-head

# Check cluster status (as the user, not root)
/home/username/ray-env/bin/ray status
```

The Ray dashboard will be available at http://head-node-ip:8265

## Ubuntu Server Notes

If you're running on Ubuntu Server, our updated scripts will:

1. Automatically detect the correct user to run Ray as (even when run with sudo)
2. Use absolute paths to the Ray executable rather than relying on environment activation
3. Set up proper environment variables in the systemd service
4. Handle permission issues with the virtual environment

If you're running on a fresh install with only the root user available, the script will warn you but set up using root (not recommended for production). Create a regular user first if possible.

## Available Tools

The following command-line tools are available:

```bash
# Run linting in parallel
ray-linter --directory ./your_project

# Run formatting in parallel
ray-formatter --directory ./your_project

# Run code indexing in parallel
ray-indexer --directory ./your_project

# Run tests in parallel
ray-tests --directory ./your_project/tests
```

## Troubleshooting

### "Ray: Command Not Found" Error

If you see an error like:
```
start_worker.sh: line 12: ray: command not found
```

This means the Ray executable is not in the PATH. The setup scripts have been updated to use absolute paths, but if you're still seeing the issue:

1. Find where Ray is installed:
   ```bash
   # Find Ray executable
   find /home -name ray -type f -executable
   ```

2. Verify Ray is installed:
   ```bash
   /path/to/ray --version
   ```

3. Update the start script to use the full path:
   ```bash
   sudo nano /home/username/ray-cluster/start_worker.sh
   # Replace "ray start" with "/full/path/to/ray start"
   ```

4. Check for permissions issues:
   ```bash
   # Make ray-env directory accessible
   sudo chmod -R 755 /home/username/ray-env
   ```

### Worker Nodes Going Offline

If worker nodes are going offline:

1. Check network connectivity between nodes with `ping`
2. Verify Ray version is the same on all nodes (2.10.0 recommended)
3. Ensure firewall allows Ray ports (6379, 8265, 10001-10999)
4. Check logs on worker nodes with: `sudo journalctl -u ray-worker -f`
5. Manually restart the worker service with: `sudo systemctl restart ray-worker`

## Manual Operation

### Starting Worker Manually

If the service approach is not working, you can start a worker manually:

```bash
# Stop the service first
sudo systemctl stop ray-worker

# Clean up /tmp/ray directory
sudo rm -rf /tmp/ray && mkdir -p /tmp/ray && chmod 777 /tmp/ray

# Start worker manually using the full path
/home/username/ray-env/bin/ray start --address='192.168.1.10:6379' --num-cpus=4 --block
```

### Service Logs

To check service logs:

```bash
# Continuous log monitoring
sudo journalctl -u ray-worker -f

# All logs for the service
sudo journalctl -u ray-worker
```

### Complete Reset

If you need to completely reset:

```bash
# Stop services
sudo systemctl stop ray-worker ray-head

# Remove /tmp/ray
sudo rm -rf /tmp/ray

# Start services
sudo systemctl start ray-head  # On head node
sudo systemctl start ray-worker  # On worker nodes
```