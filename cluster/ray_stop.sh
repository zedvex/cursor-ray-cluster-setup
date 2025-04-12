#!/bin/bash
# Ray Cluster Shutdown Script
# This script stops all Ray cluster components: head node and worker nodes

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
WORKER_NODES_FILE=${WORKER_NODES_FILE:-"worker_nodes.txt"}

echo "========================================================"
echo "Stopping Ray Cluster"
echo "========================================================"

# Check if worker nodes file exists
if [ -f "$WORKER_NODES_FILE" ] && [ -s "$WORKER_NODES_FILE" ]; then
    echo "Found worker nodes list. Stopping worker nodes first..."
    
    # Read worker nodes from file and stop Ray on each
    while read -r worker_node; do
        # Skip empty lines and comments
        if [[ -z "$worker_node" || "$worker_node" =~ ^# ]]; then
            continue
        fi
        
        echo "Stopping Ray on worker node: $worker_node"
        ssh -o StrictHostKeyChecking=no "$worker_node" "cd ~/ray-cluster && ./stop_ray.sh" &
    done < "$WORKER_NODES_FILE"
    
    # Wait for all background ssh commands to finish
    wait
    
    echo "All worker nodes stopped"
else
    echo "No worker nodes file found at $WORKER_NODES_FILE or file is empty"
    echo "Only stopping the head node"
fi

# Stop the head node
echo "Stopping Ray head node..."
ray stop

# Verify Ray is not running
if ray status 2>/dev/null | grep -q "Ray runtime started"; then
    echo "Warning: Ray is still running. Forcing stop..."
    killall -9 ray_*  # Last resort
else
    echo "Ray head node stopped successfully"
fi

echo "========================================================"
echo "Ray cluster shutdown complete"
echo "========================================================"
