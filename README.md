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
# Install Ray
pip install "ray[default]==2.10.0" pandas numpy psutil

# Start Ray head node
ray start --head --port=6379
```

### 2. Set up Worker Nodes

On each worker machine:

```bash
# Download the worker setup script
wget -O worker_setup.sh https://raw.githubusercontent.com/yourusername/cursor-ray-cluster-setup/main/cluster/worker_setup.sh

# Run the setup script (replace with your head node's IP)
sudo bash worker_setup.sh 192.168.1.10
```

### 3. Verify Cluster Setup

On the head node:

```bash
# Check cluster status
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

If worker nodes are going offline:

1. Check network connectivity between nodes
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

# Start worker manually
source ~/ray-env/bin/activate
ray start --address='192.168.1.10:6379' --num-cpus=4 --block
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