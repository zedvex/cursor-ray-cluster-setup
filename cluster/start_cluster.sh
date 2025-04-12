#!/bin/bash
# Launcher script for the Ray cluster - starts all necessary services on the head node

set -e  # Exit on error

# Load environment variables if .env exists
if [ -f ".env" ]; then
    echo "Loading environment variables from .env file"
    export $(grep -v '^#' .env | xargs)
fi

# Default values
DASHBOARD_PORT=${DASHBOARD_PORT:-8080}
API_SERVER_PORT=${API_SERVER_PORT:-8000}
RAY_DASHBOARD_PORT=${RAY_DASHBOARD_PORT:-8265}

# Check if Python virtual environment exists
if [ ! -d "$HOME/ray-env" ]; then
    echo "Error: Python virtual environment not found at ~/ray-env"
    echo "Please run the head_setup.sh script first"
    exit 1
fi

# Activate Python virtual environment
source ~/ray-env/bin/activate

# Check if Ray is already running
if ray status 2>/dev/null; then
    echo "Ray is already running."
else
    echo "Starting Ray head node..."
    ray start --head --port=6379 --dashboard-host=0.0.0.0 --dashboard-port=$RAY_DASHBOARD_PORT
    echo "Ray head node started. Dashboard available at http://$(hostname -I | awk '{print $1}'):$RAY_DASHBOARD_PORT"
fi

# Start API server in the background
echo "Starting API server on port $API_SERVER_PORT..."
nohup python api_server.py --port $API_SERVER_PORT > api_server.log 2>&1 &
API_SERVER_PID=$!
echo "API server started with PID $API_SERVER_PID"
echo "Dashboard available at http://$(hostname -I | awk '{print $1}'):$API_SERVER_PORT/dashboard"

# Write PIDs to file for later cleanup
echo $API_SERVER_PID > .api_server.pid

# Create shutdown script
cat > stop_cluster.sh << EOF
#!/bin/bash
echo "Stopping services..."

if [ -f .api_server.pid ]; then
    PID=\$(cat .api_server.pid)
    if ps -p \$PID > /dev/null; then
        echo "Stopping API server (PID \$PID)..."
        kill \$PID
    else
        echo "API server not running"
    fi
    rm .api_server.pid
fi

echo "Stopping Ray..."
ray stop

echo "All services stopped"
EOF

chmod +x stop_cluster.sh

echo "====================================================="
echo "Ray cluster started successfully!"
echo "====================================================="
echo "Ray Dashboard:  http://$(hostname -I | awk '{print $1}'):$RAY_DASHBOARD_PORT"
echo "Web Dashboard:  http://$(hostname -I | awk '{print $1}'):$API_SERVER_PORT/dashboard"
echo "API Endpoint:   http://$(hostname -I | awk '{print $1}'):$API_SERVER_PORT/v1/chat/completions"
echo ""
echo "To monitor logs:  tail -f api_server.log"
echo "To stop cluster:  ./stop_cluster.sh"
echo "====================================================="