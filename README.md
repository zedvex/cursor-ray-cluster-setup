# Cursor-Ray Cluster Setup

A comprehensive system for distributing CPU load across multiple Intel i5 machines and a NUC using Ray, integrated with Cursor IDE and Claude 3.7 Sonnet.

## Overview

This project creates a distributed computing environment that allows you to:

1. Use your NUC as the head node with Cursor IDE and Claude 3.7 Sonnet integration
2. Distribute CPU-intensive tasks across all your older Intel i5 machines
3. Share code and data via NFS for seamless development
4. Monitor the cluster performance via a web dashboard
5. Access Claude API via a Ray-powered proxy server

## System Requirements

- Head Node (NUC):
  - Ubuntu Desktop (recommended)
  - Python 3.8+
  - Cursor IDE
  - Stable internet connection for Claude API

- Worker Nodes (i5 machines):
  - Ubuntu Server (recommended)
  - Python 3.8+
  - Network connectivity to head node

## Quick Start

### 1. Set up the Head Node (NUC)

```bash
# Clone this repository
git clone https://github.com/yourusername/cursor-ray-cluster-setup.git
cd cursor-ray-cluster-setup

# Run the head node setup script
sudo ./head_setup.sh
```

This will:
- Install required packages
- Configure NFS server for code sharing
- Set up Python environment with Ray
- Create startup scripts
- Configure the firewall

### 2. Set up Worker Nodes (i5 machines)

Copy the `worker_setup.sh` script to each i5 machine and run:

```bash
# Replace with your NUC's IP address
sudo ./worker_setup.sh 192.168.1.100
```

This will:
- Install required packages
- Mount the NFS share from the head node
- Set up Python environment with Ray
- Configure automatic connection to the head node
- Set up a systemd service to start Ray on boot

### 3. Start the Ray Cluster

On the head node (NUC):

```bash
~/ray-cluster/start_head.sh
```

The worker nodes should connect automatically. Verify the connection by visiting the Ray dashboard at http://nuc-ip:8265

### 4. Start the API and Dashboard Server

```bash
cd ~/cursor-ray-cluster-setup
source ~/ray-env/bin/activate
python api_server.py
```

Access the dashboard at http://nuc-ip:8000/dashboard

## Project Structure

```
cursor-ray-cluster-setup/
├── head_setup.sh            # Setup script for head node (NUC)
├── worker_setup.sh          # Setup script for worker nodes (i5 machines)
├── .env.example             # Template for environment variables
├── ray_tasks/               # Ray task definitions
│   ├── __init__.py
│   ├── resource_utils.py    # Resource allocation utilities
│   ├── task_manager.py      # Task distribution manager
│   └── claude_api.py        # Claude API integration
├── templates/               # Web dashboard templates
│   └── dashboard.html       # Dashboard UI
├── api_server.py            # API and dashboard server
├── examples/                # Example distributed applications
│   └── file_processing.py   # Distributed file processing example
└── README.md                # This file
```

## Using the Cluster

### Processing Files in Parallel

```bash
python examples/file_processing.py /path/to/files --pattern "*.csv" --recursive
```

This will distribute the processing of all CSV files across your cluster.

### Using Claude from the Cursor IDE

1. Configure Claude API key in the `.env` file
2. Start the API server on the NUC
3. In Cursor IDE settings, set the OpenAI API endpoint to http://localhost:8000/v1
4. Cursor IDE will now use your Ray cluster to communicate with Claude

### Monitoring Cluster Performance

Visit http://nuc-ip:8000/dashboard to view:
- CPU and memory usage across the cluster
- Active worker nodes
- Running tasks

## Advanced Configuration

### Adjusting Worker Resource Limits

Edit the `resource_utils.py` file to customize how CPU and memory resources are allocated to tasks.

### Adding More Worker Nodes

Simply run the `worker_setup.sh` script on any new machine you want to add to the cluster. The head node will automatically detect and utilize the new resources.

### Customizing the Claude API Integration

Edit the `claude_api.py` file to adjust retry logic, batch processing, or other parameters.

## Troubleshooting

### Worker Not Connecting

1. Check network connectivity between the worker and head node
2. Verify the firewall settings allow connections on ports 6379 and 10001
3. Check the Ray logs: `systemctl status ray-worker` on the worker node

### NFS Mount Issues

Ensure the NFS server is running on the head node:
```bash
sudo systemctl status nfs-kernel-server
```

On worker nodes, try manually mounting:
```bash
sudo mount -t nfs <head-node-ip>:/mnt/code /mnt/code
```

### API Server Not Starting

Check for port conflicts:
```bash
sudo netstat -tuln | grep 8000
```

## License

MIT