# Simplified Ray Cluster Setup

A minimalist system for distributing CPU-intensive workloads across multiple machines using Ray.

## Overview

This project creates a basic distributed computing environment that:

1. Uses a head node to coordinate Ray cluster operations
2. Distributes tasks across worker nodes
3. Provides tools for distributed code processing (linting, formatting, indexing, testing)

## System Requirements

### Head Node Requirements
- Ubuntu 20.04+ or other Linux distro
- Python 3.8+
- 8GB+ RAM recommended

### Worker Node Requirements
- Ubuntu 20.04+ or other Linux distro
- Python 3.8+
- 4GB+ RAM
- Network connectivity to head node

## Quick Start

### 1. Set up the Head Node

```bash
# Clone this repository
git clone https://github.com/yourusername/cursor-ray-cluster-setup.git
cd cursor-ray-cluster-setup

# Run the head setup script
sudo bash cluster/head_setup.sh
```

### 2. Set up Worker Nodes

On each worker machine:

```bash
# Clone the repository
git clone https://github.com/yourusername/cursor-ray-cluster-setup.git
cd cursor-ray-cluster-setup

# Run the worker setup script (replace with your head node's IP)
sudo bash cluster/worker_setup.sh 192.168.1.10
```

### 3. Verify Cluster Setup

On the head node:

```bash
# Check cluster status
sudo systemctl status ray-head
ray status
```

The Ray dashboard will be available at http://head-node-ip:8265

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

1. Verify Ray is installed in the virtual environment:
   ```bash
   /home/username/ray-env/bin/ray --version
   ```

2. Update the start script to use the full path:
   ```bash
   sudo nano ~/ray-cluster/start_worker.sh
   # Replace "ray start" with "/home/username/ray-env/bin/ray start"
   ```

3. Add the Python environment to the system service:
   ```bash
   sudo systemctl edit ray-worker
   ```
   
   Add:
   ```
   [Service]
   Environment="PATH=/home/username/ray-env/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
   Environment="PYTHONPATH=/home/username/ray-env/lib/python3.8/site-packages"
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

If the service approach is not working, you can manually start a worker:

```bash
# Stop the service first
sudo systemctl stop ray-worker

# Start worker manually using the full path
/home/username/ray-env/bin/ray start --address='192.168.1.10:6379' --num-cpus=4 --block
```

### Simplified Ray Commands

```bash
# Start head node
ray start --head --port=6379

# Start worker node
ray start --address='192.168.1.10:6379' --block

# Stop Ray
ray stop

# Check status
ray status
```