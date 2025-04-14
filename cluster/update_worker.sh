#!/bin/bash
# This script updates the Ray worker node with the latest changes and restarts the worker
# Usage: ./update_worker.sh [HEAD_NODE_IP]

set -e
cd "$(dirname "$0")/.."

# Default head node IP or use provided argument
HEAD_NODE_IP=${1:-"192.168.1.10"}
echo "Using head node IP: $HEAD_NODE_IP"

# Stop any existing Ray processes
echo "Stopping any existing Ray processes..."
if [ -d "$HOME/ray-env" ]; then
  source "$HOME/ray-env/bin/activate"
  ray stop 2>/dev/null || true
fi
tmux kill-session -t ray-worker 2>/dev/null || true
sleep 2

echo "Pulling latest changes from git..."
git pull

echo "Making scripts executable..."
chmod +x cluster/worker_setup.sh
chmod +x cluster/update_worker.sh

echo "Running worker setup script..."
sudo bash cluster/worker_setup.sh "$HEAD_NODE_IP"

echo "================================================================"
echo "Update complete! Ray worker is now running in tmux session."
echo "- To view the worker: tmux attach -t ray-worker"
echo "- To check status:    ~/ray-cluster/check_ray.sh"
echo "================================================================" 