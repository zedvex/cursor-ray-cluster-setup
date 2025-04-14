#!/bin/bash
# Signal-handling Ray worker script

# Set up logging
mkdir -p ~/ray-cluster/logs
LOG_FILE=~/ray-cluster/logs/signal-worker-$(date +%Y%m%d-%H%M%S).log

# Log both stdout and stderr to the log file
exec > >(tee -a "$LOG_FILE") 2>&1

echo "================== Signal-Managed Ray Worker =================="
echo "Start time: $(date)"

# Activate the virtual environment
source ~/ray-env/bin/activate

# Stop any existing Ray processes
echo "Stopping any existing Ray processes..."
ray stop 2>/dev/null || true
sleep 5

# Clean up Ray directory
echo "Cleaning Ray directory..."
rm -rf /tmp/ray
mkdir -p /tmp/ray
chmod 777 /tmp/ray

# Create Python wrapper script for better signal handling
PYTHON_SCRIPT=~/ray-cluster/ray_wrapper.py
cat > $PYTHON_SCRIPT << 'PYTHONEOF'
#!/usr/bin/env python3
"""
Ray worker wrapper with proper signal handling
"""
import os
import sys
import time
import signal
import subprocess
import logging

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Ray process global reference
ray_process = None

def signal_handler(sig, frame):
    """Handle signals gracefully"""
    logging.info(f"Received signal {sig}, shutting down gracefully")
    global ray_process
    
    if ray_process:
        logging.info("Stopping Ray process...")
        # First try to stop Ray gracefully
        try:
            subprocess.run(["ray", "stop"], timeout=10)
            logging.info("Ray stopped with ray stop command")
        except Exception as e:
            logging.warning(f"Error stopping Ray: {e}")
        
        # Then kill the process if it's still running
        if ray_process.poll() is None:
            logging.info("Terminating Ray process...")
            ray_process.terminate()
            try:
                ray_process.wait(timeout=10)
                logging.info("Ray process terminated")
            except subprocess.TimeoutExpired:
                logging.warning("Ray process did not terminate, forcing kill")
                ray_process.kill()
    
    logging.info("Exiting wrapper")
    sys.exit(0)

def main():
    # Register signal handlers
    for sig in [signal.SIGINT, signal.SIGTERM, signal.SIGHUP]:
        signal.signal(sig, signal_handler)
    
    # Get head node address from environment
    head_node = os.environ.get('RAY_HEAD_NODE', '192.168.1.10:6379')
    logging.info(f"Connecting to Ray head node at {head_node}")
    
    # Start Ray process
    global ray_process
    cmd = [
        "ray", "start", 
        f"--address={head_node}",
        "--num-cpus=2",
        "--resources={\"worker_node\": 1.0}", 
        "--block"
    ]
    
    logging.info(f"Starting Ray with command: {' '.join(cmd)}")
    ray_process = subprocess.Popen(cmd)
    
    # Wait for Ray process to exit
    try:
        exit_code = ray_process.wait()
        logging.info(f"Ray process exited with code {exit_code}")
    except KeyboardInterrupt:
        logging.info("Keyboard interrupt received")
        signal_handler(signal.SIGINT, None)
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
PYTHONEOF

chmod +x $PYTHON_SCRIPT

# Set head node environment variable
export RAY_HEAD_NODE="192.168.1.10:6379"

# Run the Python wrapper
echo "Starting Ray with signal handling via Python wrapper..."
python3 $PYTHON_SCRIPT

# This should not be reached unless the script exits
echo "Wrapper script exited with code $?"
echo "==================== Worker Stopped ====================" 