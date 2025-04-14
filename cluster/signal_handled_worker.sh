#!/bin/bash
# Signal-handling Ray worker script

# Set up logging
mkdir -p ~/ray-cluster/logs
LOG_FILE=~/ray-cluster/logs/signal-worker-$(date +%Y%m%d-%H%M%S).log

# Log both stdout and stderr to the log file
exec > >(tee -a "$LOG_FILE") 2>&1

echo "================== Signal-Managed Ray Worker =================="
echo "Start time: $(date)"

# Increase network buffers to prevent heartbeat timeouts
echo "Configuring network parameters for better heartbeat reliability..."
sudo sysctl -w net.core.rmem_max=16777216
sudo sysctl -w net.core.wmem_max=16777216
sudo sysctl -w net.core.rmem_default=1048576
sudo sysctl -w net.core.wmem_default=1048576
sudo sysctl -w net.ipv4.tcp_rmem="4096 1048576 16777216"
sudo sysctl -w net.ipv4.tcp_wmem="4096 1048576 16777216"
sudo sysctl -w net.ipv4.tcp_keepalive_time=60
sudo sysctl -w net.ipv4.tcp_keepalive_intvl=10
sudo sysctl -w net.ipv4.tcp_keepalive_probes=6

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
Ray worker wrapper with proper signal handling and heartbeat configuration
"""
import os
import sys
import time
import signal
import subprocess
import logging
import platform
import json

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

def get_system_info():
    """Log system information for diagnostics"""
    info = {
        "hostname": platform.node(),
        "platform": platform.platform(),
        "python_version": platform.python_version(),
    }
    
    # Get Ray version
    try:
        ray_version = subprocess.check_output(["ray", "--version"], text=True).strip()
        info["ray_version"] = ray_version
    except Exception as e:
        info["ray_version"] = f"Error getting Ray version: {e}"
    
    # Get memory info
    try:
        with open("/proc/meminfo", "r") as f:
            mem_info = {}
            for line in f:
                if "MemTotal" in line or "MemAvailable" in line:
                    parts = line.split()
                    mem_info[parts[0].rstrip(":")] = parts[1]
            info["memory"] = mem_info
    except Exception as e:
        info["memory"] = f"Error getting memory info: {e}"
    
    return info

def create_ray_config():
    """Create custom Ray config file with optimized heartbeat settings"""
    ray_config = {
        "raylet": {
            "num_heartbeats_timeout": 60,  # Increase timeout for missing heartbeats (default is 30)
            "heartbeat_timeout_milliseconds": 10000,  # 10 seconds instead of default 1 second
            "initial_reconstruction_timeout_milliseconds": 10000,
            "free_objects_period_milliseconds": 1000,
        },
        "ray_client_server": {
            "timeout_seconds": 60
        },
        "gcs_server": {
            "grpc_server_reconnect_timeout_s": 60,
            "internal_gcs_client_reconnect_timeout_s": 60
        }
    }
    
    config_path = os.path.expanduser("~/ray_custom_config.yaml")
    with open(config_path, "w") as f:
        import yaml
        yaml.dump(ray_config, f)
    
    return config_path

def main():
    # Log system info
    logging.info(f"System info: {json.dumps(get_system_info(), indent=2)}")
    
    # Create custom Ray config with longer heartbeat timeouts
    config_path = create_ray_config()
    logging.info(f"Created custom Ray config at {config_path}")
    
    # Register signal handlers
    for sig in [signal.SIGINT, signal.SIGTERM, signal.SIGHUP]:
        signal.signal(sig, signal_handler)
    
    # Get head node address from environment
    head_node = os.environ.get('RAY_HEAD_NODE', '192.168.1.10:6379')
    logging.info(f"Connecting to Ray head node at {head_node}")
    
    # Set environment variables to increase heartbeat timeouts
    os.environ["RAY_timeout_ms"] = "10000"
    os.environ["RAY_HEARTBEAT_TIMEOUT_S"] = "60"
    os.environ["RAY_worker_register_timeout_seconds"] = "60"
    
    # Start Ray process with increased heartbeat tolerance
    global ray_process
    cmd = [
        "ray", "start", 
        f"--address={head_node}",
        "--num-cpus=2",
        "--resources={\"worker_node\": 1.0}",
        f"--config={config_path}",
        "--log-style=pretty",
        "--log-color=False",
        "--block"
    ]
    
    logging.info(f"Starting Ray with command: {' '.join(cmd)}")
    
    # Periodically restart Ray to maintain connection
    while True:
        try:
            ray_process = subprocess.Popen(cmd)
            logging.info("Ray process started")
            
            # Check if process is still running every 30 seconds
            while ray_process.poll() is None:
                logging.info("Heartbeat: Ray process is still running")
                time.sleep(30)
            
            exit_code = ray_process.returncode
            logging.info(f"Ray process exited with code {exit_code}")
            
            # If process exited, wait and restart
            logging.info("Waiting 30 seconds before restarting Ray...")
            time.sleep(30)
            
        except KeyboardInterrupt:
            logging.info("Keyboard interrupt received")
            signal_handler(signal.SIGINT, None)
            break
    
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