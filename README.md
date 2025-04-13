# Cursor-Ray Cluster Setup

A comprehensive system for distributing CPU-intensive workloads across multiple machines using Ray, integrated with Cursor IDE and Claude 3.7 Sonnet for enhanced AI-assisted development.

## Overview

This project creates a distributed computing environment that allows you to:

1. Use a powerful machine as the head node with Cursor IDE and Claude 3.7 Sonnet integration
2. Distribute CPU-intensive tasks (code indexing, linting, formatting, testing) across worker machines
3. Share code and data via NFS for seamless development
4. Monitor cluster performance via a web dashboard
5. Access Claude API via a Ray-powered proxy server

## System Requirements

### Head Node Requirements
- Ubuntu 20.04+ or Windows 10/11 with WSL2
- Python 3.8+
- Cursor IDE
- 8GB+ RAM recommended
- Stable internet connection for Claude API

### Worker Node Requirements
- Ubuntu 20.04+ or any Linux distribution with Python support
- Python 3.8+
- 4GB+ RAM
- Network connectivity to head node

## Quick Start

### 1. Set up the Head Node

```bash
# Clone this repository
git clone https://github.com/yourusername/cursor-ray-cluster-setup.git
cd cursor-ray-cluster-setup

# Run the head node setup script
sudo ./head_setup.sh
```

This will:
- Install required packages (Ray, FastAPI, uvicorn, etc.)
- Configure NFS server for code sharing
- Set up Python environment with Ray
- Create startup scripts for the head node
- Configure firewall rules for Ray

### 2. Set up Worker Nodes

Copy the `worker_setup.sh` script to each worker machine and run:

```bash
# Replace with your head node's IP address
sudo ./worker_setup.sh 192.168.1.100
```

This will:
- Install required packages
- Mount the NFS share from the head node
- Set up Python environment with Ray
- Configure automatic connection to the head node
- Set up a systemd service to start Ray on boot

### 3. Start the Ray Cluster

On the head node:

```bash
~/ray-cluster/start_head.sh
```

The worker nodes should connect automatically. Verify the connection by visiting the Ray dashboard at http://head-node-ip:8265

### 4. Start the Ray Proxy Server for Claude API

```bash
cd ~/cursor-ray-cluster-setup
source ~/ray-env/bin/activate
python scripts/start_ray_proxy.py
```

## Project Structure

```
cursor-ray-cluster-setup/
├── head_setup.sh              # Setup script for head node
├── worker_setup.sh            # Setup script for worker nodes
├── .env.example               # Template for environment variables
├── setup.py                   # Package setup file
├── ray_tasks/                 # Ray task definitions
│   ├── __init__.py
│   ├── resource_utils.py      # Resource allocation utilities
│   ├── task_manager.py        # Task distribution manager
│   └── claude_api.py          # Claude API integration
├── scripts/                   # Utility scripts
│   ├── start_ray_proxy.py     # Script to start the Ray proxy for Claude API
│   ├── benchmarks.py          # Benchmarking utilities for the Ray cluster
│   ├── run_linter.py          # Run code linters in parallel using Ray
│   ├── run_formatter.py       # Run code formatters in parallel using Ray
│   ├── run_indexer.py         # Index code in parallel using Ray
│   └── run_tests.py           # Run tests in parallel using Ray
├── templates/                 # Web dashboard templates
│   └── dashboard.html         # Dashboard UI
├── api/                       # API endpoints
│   ├── __init__.py
│   ├── proxy.py               # Claude API proxy
│   └── dashboard.py           # Dashboard API endpoints
├── examples/                  # Example distributed applications
│   └── file_processing.py     # Distributed file processing example
└── README.md                  # This file
```

## Features

### Distributed Code Processing

The system provides several utilities for distributed code processing:

#### Code Linting

```bash
ray-linter --directory ./your_project --formatters flake8,pylint,mypy --output lint_results.json
```

Distributes linting tasks across the cluster for faster code quality checks.

#### Code Formatting

```bash
ray-formatter --directory ./your_project --formatters black,isort --check-only
```

Formats Python code in parallel using black and isort.

#### Code Indexing

```bash
ray-indexer --directory ./your_project --output index.json --include-docstrings --include-imports
```

Creates a code index for navigation and documentation in parallel.

#### Parallel Testing

```bash
ray-tests --directory ./your_project/tests --verbose
```

Distributes test execution across the cluster.

### Benchmarking

The system includes benchmarking tools to measure performance:

```bash
python scripts/benchmarks.py --include latency,throughput,resource,data_transfer --iterations 10
```

Available benchmarks:
- Task latency
- Task throughput
- CPU/memory utilization
- Data transfer performance

### Claude API Integration

The Ray proxy server allows Cursor IDE to communicate with Claude API while distributing processing across the cluster:

1. Configure Claude API key in the `.env` file
2. Start the Ray proxy server on the head node
3. In Cursor IDE settings, set the API endpoint to `http://localhost:8000/v1`

## Advanced Configuration

### Customizing Ray Configuration

Edit the `ray_tasks/resource_utils.py` file to customize:
- CPU and memory allocations
- Object store size
- Custom resources

### Scaling the Cluster

#### Adding More Worker Nodes

Simply run the `worker_setup.sh` script on any new machine you want to add to the cluster. The head node will automatically detect and utilize the new resources.

#### Using Cloud Instances

The setup can be adapted for cloud environments:
1. Set up a head node on a cloud VM
2. Configure security groups/firewall rules to allow Ray ports (6379, 8265, 10001-10999)
3. Launch worker instances and run the worker setup script

### Performance Optimization

#### Resource Allocation

Adjust worker node CPU/memory allocation in `ray_tasks/resource_utils.py`:

```python
def configure_resources():
    # Customize based on your hardware
    return {
        "num_cpus": os.cpu_count() - 1,  # Reserve 1 CPU for system
        "memory": int(psutil.virtual_memory().total * 0.8),  # Use 80% of memory
    }
```

#### Job Scheduling

For large workloads, use the Ray Job Submission API:

```python
from ray.job_submission import JobSubmissionClient
client = JobSubmissionClient("http://head-node-ip:8265")
job_id = client.submit_job(
    entrypoint="python scripts/run_indexer.py --directory /path/to/large/project",
    runtime_env={"working_dir": "."}
)
```

## Troubleshooting

### Worker Node Connection Issues

If worker nodes aren't connecting:
1. Check network connectivity: `ping head-node-ip`
2. Verify firewall settings: `sudo ufw status`
3. Check Ray logs on worker: `sudo journalctl -u ray-worker`
4. Ensure ports 6379 and 10001-10999 are open

### NFS Mount Issues

If code sharing via NFS is not working:
```bash
# On head node
sudo systemctl status nfs-kernel-server

# On worker node
sudo mount -t nfs head-node-ip:/mnt/code /mnt/code -v
```

### Ray Dashboard Not Accessible

If you can't access the dashboard:
1. Check that the dashboard is running: `ps aux | grep ray::dashboard`
2. Verify the dashboard port is open: `sudo ufw status | grep 8265`
3. Try accessing from the head node itself: `curl localhost:8265`

### Claude API Proxy Issues

If the proxy server isn't working:
1. Check that the proxy is running: `ps aux | grep start_ray_proxy`
2. Verify your Claude API key in the `.env` file
3. Check proxy logs for errors

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Commit your changes: `git commit -am 'Add my feature'`
4. Push to the branch: `git push origin feature/my-feature`
5. Submit a Pull Request

## License

MIT