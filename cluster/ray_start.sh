#!/bin/bash
# Ray Cluster Startup Script
# This script starts all Ray cluster components: head node and worker nodes

set -e  # Exit immediately if a command exits with a non-zero status

# Load environment variables if .env file exists
if [ -f .env ]; then
    echo "Loading environment variables from .env"
    set -a
    source .env
    set +a
else
    echo "No .env file found, using default settings"
fi

# Default settings
HEAD_NODE_IP=${RAY_HEAD_IP:-$(hostname -I | awk '{print $1}')}
HEAD_NODE_PORT=${RAY_HEAD_PORT:-6379}
DASHBOARD_PORT=${RAY_DASHBOARD_PORT:-8265}
WORKER_NODES_FILE=${WORKER_NODES_FILE:-"worker_nodes.txt"}

echo "========================================================"
echo "Starting Ray Cluster"
echo "Head Node IP: $HEAD_NODE_IP"
echo "Head Node Port: $HEAD_NODE_PORT"
echo "Dashboard Port: $DASHBOARD_PORT"
echo "========================================================"

# Check if Ray is already running
if ray status 2>/dev/null | grep -q "Ray runtime started"; then
    echo "Ray is already running. Stopping existing cluster first..."
    ray stop
    sleep 2
fi

# Start Ray head node
echo "Starting Ray head node..."
ray start --head \
    --port=$HEAD_NODE_PORT \
    --dashboard-host=0.0.0.0 \
    --dashboard-port=$DASHBOARD_PORT \
    --include-dashboard=true

# Verify head node is running
if ! ray status 2>/dev/null | grep -q "Ray runtime started"; then
    echo "Error: Failed to start Ray head node"
    exit 1
fi

echo "Ray head node started successfully"
echo "Dashboard available at: http://$HEAD_NODE_IP:$DASHBOARD_PORT"

# Check if we need to connect worker nodes
if [ -f "$WORKER_NODES_FILE" ] && [ -s "$WORKER_NODES_FILE" ]; then
    echo "Found worker nodes list. Attempting to connect worker nodes..."
    
    # Read worker nodes from file and connect them
    while read -r worker_node; do
        # Skip empty lines and comments
        if [[ -z "$worker_node" || "$worker_node" =~ ^# ]]; then
            continue
        fi
        
        echo "Connecting worker node: $worker_node"
        ssh -o StrictHostKeyChecking=no "$worker_node" "cd ~/ray-cluster && ./start_worker.sh $HEAD_NODE_IP:$HEAD_NODE_PORT" &
    done < "$WORKER_NODES_FILE"
    
    # Wait for all background ssh commands to finish
    wait
    
    echo "All worker nodes connected"
else
    echo "No worker nodes file found at $WORKER_NODES_FILE or file is empty"
    echo "Only the head node is running"
fi

# Print cluster status
echo "========================================================"
echo "Ray Cluster Status:"
ray status
echo "========================================================"
echo ""
echo "Ray cluster is now running"
echo "To stop the cluster, run: ./ray_stop.sh"
echo "========================================================"
