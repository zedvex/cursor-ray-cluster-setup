#!/bin/bash
# Simple Ray head node setup script

set -e  # Exit on any error
set -x  # Print commands for debugging

# Check if script is run as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root or with sudo"
    exit 1
fi

# Determine the actual user (even when run with sudo)
if [ -n "$SUDO_USER" ]; then
    ACTUAL_USER=$SUDO_USER
else
    ACTUAL_USER=$(whoami)
fi

echo "Setting up Ray head node for user: $ACTUAL_USER"
HOME_DIR=$(eval echo ~$ACTUAL_USER)
echo "Home directory: $HOME_DIR"

# Check if user is root (not recommended but allowed)
if [ "$ACTUAL_USER" = "root" ]; then
    echo "WARNING: Setting up using root user is not recommended for production"
    echo "Consider creating a regular user first"
fi

# Create directories
mkdir -p $HOME_DIR/ray-cluster
CLUSTER_DIR="$HOME_DIR/ray-cluster"
ENV_DIR="$HOME_DIR/ray-env"

# Install system dependencies
apt-get update
apt-get install -y python3 python3-pip python3-venv sudo curl

# Create virtual environment
python3 -m venv $ENV_DIR
chown -R $ACTUAL_USER:$ACTUAL_USER $ENV_DIR
chown -R $ACTUAL_USER:$ACTUAL_USER $CLUSTER_DIR

# Upgrade pip and install Ray
sudo -u $ACTUAL_USER $ENV_DIR/bin/pip install --upgrade pip

# Install Ray with necessary dependencies
sudo -u $ACTUAL_USER $ENV_DIR/bin/pip install 'ray[default]>=2.31.0' fastapi uvicorn pandas numpy psutil prometheus-client

# Verify Ray installation
echo "Verifying Ray installation..."
RAY_PATH=$(sudo -u $ACTUAL_USER $ENV_DIR/bin/which ray || echo "NOT_FOUND")
if [ "$RAY_PATH" = "NOT_FOUND" ]; then
    RAY_PATH=$(find $ENV_DIR -name ray -type f -executable | head -1)
    if [ -z "$RAY_PATH" ]; then
        echo "ERROR: Ray executable not found! Installation may have failed."
        echo "Checking pip list for Ray package:"
        sudo -u $ACTUAL_USER $ENV_DIR/bin/pip list | grep -i ray
        echo "Trying to locate manually:"
        find $ENV_DIR -name "*ray*" -type d | sort
        exit 1
    fi
    echo "Found Ray at: $RAY_PATH"
else
    echo "Ray executable found at: $RAY_PATH"
fi

# Verify Ray version
echo "Ray version:"
sudo -u $ACTUAL_USER $ENV_DIR/bin/ray --version

# Configure system limits for Ray
cat > /etc/security/limits.d/ray.conf << EOF
$ACTUAL_USER soft nofile 65536
$ACTUAL_USER hard nofile 65536
EOF

# Set firewall rules if UFW is enabled
if command -v ufw &> /dev/null && ufw status | grep -q "active"; then
    echo "Configuring firewall rules for Ray..."
    ufw allow 6379/tcp  # Redis port for Ray
    ufw allow 8265/tcp  # Ray Dashboard
    ufw allow 10001/tcp # Ray Object Manager
    ufw allow 8000/tcp  # FastAPI
    ufw allow 9100/tcp  # Node Exporter
fi

# Clean up existing Ray directories
if [ -d "/tmp/ray" ]; then
    OWNER=$(stat -c '%U' /tmp/ray)
    if [ "$OWNER" != "$ACTUAL_USER" ]; then
        echo "Removing /tmp/ray directory owned by $OWNER"
        rm -rf /tmp/ray
    fi
fi

# Create fresh /tmp/ray directory with correct permissions
mkdir -p /tmp/ray
chmod 1777 /tmp/ray
chown $ACTUAL_USER:$ACTUAL_USER /tmp/ray

# Create head node start script
cat > $CLUSTER_DIR/start_head.sh << EOF
#!/bin/bash
set -x  # Print commands for debugging

# Verify working directory
echo "Current directory: \$(pwd)"
echo "User: \$(whoami)"

# Make sure the Ray directory exists with proper permissions
if [ ! -d "/tmp/ray" ]; then
    mkdir -p /tmp/ray
    chmod 1777 /tmp/ray
fi

# Set PATH to include Ray executable
export PATH="$ENV_DIR/bin:\$PATH"
RAY_PATH="$ENV_DIR/bin/ray"

# Verify Ray is available
echo "Checking Ray executable:"
ls -la \$RAY_PATH
\$RAY_PATH --version

# Get local IP address
CURRENT_IP=\$(hostname -I | awk '{print \$1}')
echo "Starting Ray head node on \$CURRENT_IP"

# Make scripts directory
mkdir -p $CLUSTER_DIR/scripts

# Start the Ray head node
\$RAY_PATH start --head \\
    --port=6379 \\
    --dashboard-host=0.0.0.0 \\
    --dashboard-port=8265 \\
    --num-cpus=4 \\
    --resources='{"head_node": 1.0}' \\
    --block
EOF

# Create FastAPI server script
cat > $CLUSTER_DIR/scripts/start_ray_proxy.py << EOF
#!/usr/bin/env python3
"""
Ray API proxy server built with FastAPI.
Provides REST API access to Ray cluster functionality.
"""

import argparse
import asyncio
import logging
import os
import ray
import uvicorn
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Dict, List, Any, Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("ray-proxy")

app = FastAPI(title="Ray Cluster API", 
              description="REST API for Ray distributed computing tasks")

# Cluster connection status
is_connected = False

# Parse arguments
def parse_args():
    parser = argparse.ArgumentParser(description="Ray Proxy API Server")
    parser.add_argument("--ray-address", default="auto",
                      help="The Ray cluster address (default: auto)")
    parser.add_argument("--host", default="0.0.0.0",
                      help="The host to bind the server to (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000,
                      help="The port to bind the server to (default: 8000)")
    parser.add_argument("--log-level", default="info",
                      choices=["debug", "info", "warning", "error", "critical"],
                      help="The log level (default: info)")
    return parser.parse_args()

# Connect to Ray cluster
def connect_to_ray(address: str) -> bool:
    global is_connected
    try:
        logger.info(f"Connecting to Ray cluster at: {address}")
        ray.init(address=address)
        is_connected = True
        logger.info(f"Successfully connected to Ray cluster: {ray.cluster_resources()}")
        return True
    except Exception as e:
        logger.error(f"Failed to connect to Ray cluster: {e}")
        is_connected = False
        return False

# API response models
class ClusterInfoResponse(BaseModel):
    available_resources: Dict
    total_resources: Dict
    cluster_status: str

class TaskRequest(BaseModel):
    function_name: str
    args: List[Any] = []
    kwargs: Dict[str, Any] = {}
    num_returns: int = 1
    num_cpus: Optional[float] = None
    resources: Optional[Dict[str, float]] = None

class TaskResponse(BaseModel):
    task_id: str
    status: str

# API endpoints
@app.get("/")
async def root():
    return {"status": "ok", "message": "Ray Proxy API is running"}

@app.get("/api/v1/status")
async def get_status():
    if not is_connected:
        raise HTTPException(status_code=503, detail="Not connected to Ray cluster")
    
    try:
        return {
            "status": "ok",
            "ray_version": ray.__version__,
            "connected": is_connected
        }
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/cluster/info", response_model=ClusterInfoResponse)
async def get_cluster_info():
    if not is_connected:
        raise HTTPException(status_code=503, detail="Not connected to Ray cluster")
    
    try:
        return {
            "available_resources": ray.available_resources(),
            "total_resources": ray.cluster_resources(),
            "cluster_status": "healthy"  # This is a placeholder - you might want to implement a real health check
        }
    except Exception as e:
        logger.error(f"Error getting cluster info: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/tasks", response_model=TaskResponse)
async def submit_task(task: TaskRequest, background_tasks: BackgroundTasks):
    if not is_connected:
        raise HTTPException(status_code=503, detail="Not connected to Ray cluster")
    
    try:
        # For now, we'll just acknowledge the task
        # In a real implementation, you would execute the task remotely
        return {
            "task_id": f"task_{task.function_name}_{id(task)}",
            "status": "acknowledged"
        }
    except Exception as e:
        logger.error(f"Error submitting task: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Main function
def main():
    args = parse_args()
    
    # Set log level
    numeric_level = getattr(logging, args.log_level.upper(), None)
    if isinstance(numeric_level, int):
        logger.setLevel(numeric_level)
    
    # Connect to Ray
    if not connect_to_ray(args.ray_address):
        logger.warning("Failed to connect to Ray cluster. Starting anyway.")
    
    # Start the server
    logger.info(f"Starting server on {args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)

if __name__ == "__main__":
    main()
EOF

# Make scripts executable
chmod +x $CLUSTER_DIR/start_head.sh
chmod +x $CLUSTER_DIR/scripts/start_ray_proxy.py
chown -R $ACTUAL_USER:$ACTUAL_USER $CLUSTER_DIR

# Test the head script without blocking
echo "Testing the head script (non-blocking)..."
TEST_CMD="$CLUSTER_DIR/start_head.sh"
TEST_CMD=${TEST_CMD/--block/--block-for-10-seconds}
sudo -u $ACTUAL_USER bash -c "cd $CLUSTER_DIR && $TEST_CMD" || echo "Test exited with $?"

# Create systemd service for Ray head node
cat > /etc/systemd/system/ray-head.service << EOF
[Unit]
Description=Ray Head Node
After=network.target
StartLimitIntervalSec=500
StartLimitBurst=5

[Service]
Type=simple
User=$ACTUAL_USER
Group=$ACTUAL_USER
WorkingDirectory=$CLUSTER_DIR
Environment="PATH=$ENV_DIR/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStartPre=-/bin/sh -c "if [ ! -d /tmp/ray ]; then mkdir -p /tmp/ray && chmod 1777 /tmp/ray && chown $ACTUAL_USER:$ACTUAL_USER /tmp/ray; fi"
ExecStart=$CLUSTER_DIR/start_head.sh
ExecStop=/bin/kill -TERM \$MAINPID
Restart=on-failure
RestartSec=30
KillMode=mixed
KillSignal=SIGTERM
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
EOF

# Create systemd service for Ray API proxy
cat > /etc/systemd/system/ray-proxy.service << EOF
[Unit]
Description=Ray API Proxy
After=ray-head.service
Requires=ray-head.service

[Service]
Type=simple
User=$ACTUAL_USER
Group=$ACTUAL_USER
WorkingDirectory=$CLUSTER_DIR
Environment="PATH=$ENV_DIR/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStart=$ENV_DIR/bin/python $CLUSTER_DIR/scripts/start_ray_proxy.py --ray-address=localhost:6379
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Create node exporter service
cat > /etc/systemd/system/node-exporter.service << EOF
[Unit]
Description=Node Exporter
After=network.target

[Service]
Type=simple
User=$ACTUAL_USER
Group=$ACTUAL_USER
WorkingDirectory=$CLUSTER_DIR
ExecStart=$ENV_DIR/bin/python3 -m prometheus_client.exposition --port 9100

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd configuration
systemctl daemon-reload

# Enable and start services
systemctl enable ray-head.service
systemctl start ray-head.service
systemctl enable ray-proxy.service
systemctl start ray-proxy.service
systemctl enable node-exporter.service
systemctl start node-exporter.service

# Print service status
echo "Ray Head service status:"
systemctl status ray-head.service

echo "Ray Proxy service status:"
systemctl status ray-proxy.service

echo "Node Exporter service status:"
systemctl status node-exporter.service

echo "-------------------------------------------"
echo "Head node setup complete!"
echo "-------------------------------------------"
echo "Ray dashboard available at: http://$(hostname -I | awk '{print $1}'):8265"
echo "Ray Proxy API available at: http://$(hostname -I | awk '{print $1}'):8000"
echo "Node metrics available at: http://$(hostname -I | awk '{print $1}'):9100/metrics"
echo ""
echo "To connect worker nodes, run the worker setup script on each worker node:"
echo "$ sudo ./worker_setup.sh $(hostname -I | awk '{print $1}')"
echo ""
echo "For manual operation, you can use:"
echo "$ sudo systemctl stop ray-head # To stop the head node"
echo "$ sudo systemctl start ray-head # To start the head node"
echo "$ sudo systemctl status ray-head # To check the head node status"
echo ""
echo "To check service logs:"
echo "$ sudo journalctl -u ray-head -f # To follow head node logs"
echo "$ sudo journalctl -u ray-proxy -f # To follow API proxy logs"