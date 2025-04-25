#!/bin/bash
# Docker-based Ray Head Node Setup
# For Intel i5-7500 4C/4T @ 3.8GHz with 24GB RAM

set -e  # Exit on error

# Configuration
IP_ADDRESS="192.168.1.10"
RAY_PORT=6379
DASHBOARD_PORT=8265
API_PORT=8000
CPU_LIMIT=2
MEMORY_LIMIT=16g  # Docker format
USERNAME=$(whoami)

# Create directories
echo "Creating directories..."
sudo mkdir -p /opt/ai-cluster/{config,data,logs,shared,workflows,models}
sudo chown -R $USERNAME:$USERNAME /opt/ai-cluster

# Create docker-compose.yml file
echo "Creating docker-compose configuration..."
cat > /opt/ai-cluster/docker-compose.yml << EOF
version: '3'

services:
  ray-head:
    image: rayproject/ray:2.9.0
    container_name: ray-head
    ports:
      - "${RAY_PORT}:${RAY_PORT}"
      - "${DASHBOARD_PORT}:${DASHBOARD_PORT}"
    command: >
      ray start --head
      --port=${RAY_PORT}
      --dashboard-host=0.0.0.0
      --dashboard-port=${DASHBOARD_PORT}
      --num-cpus=${CPU_LIMIT}
      --resources='{"memory": 16000, "head": 1.0}'
      --object-store-memory=6000000000
      --include-dashboard=true
    volumes:
      - /opt/ai-cluster/shared:/root/shared
      - /opt/ai-cluster/logs:/tmp/ray
    network_mode: "host"
    restart: unless-stopped

  # API Gateway service (FastAPI)
  api-gateway:
    image: python:3.10-slim
    container_name: api-gateway
    ports:
      - "${API_PORT}:${API_PORT}"
    volumes:
      - /opt/ai-cluster:/opt/ai-cluster
    working_dir: /opt/ai-cluster/api_gateway
    command: >
      bash -c "
        pip install fastapi uvicorn ray anthropic openai httpx &&
        echo 'Waiting for Ray to start...' &&
        sleep 10 &&
        echo 'Starting API Gateway...' &&
        python -c 'import os; os.makedirs(\"app\", exist_ok=True)' &&
        echo 'from fastapi import FastAPI; import ray; app = FastAPI(); ray.init(address=\"${IP_ADDRESS}:${RAY_PORT}\"); @app.get(\"/\"); def read_root(): return {\"status\": \"ok\", \"ray_address\": \"${IP_ADDRESS}:${RAY_PORT}\"}' > app/main.py &&
        uvicorn app.main:app --host 0.0.0.0 --port ${API_PORT}"
    depends_on:
      - ray-head
    network_mode: "host"
    restart: unless-stopped

  # Monitoring service (Prometheus + Grafana)
  monitoring:
    image: prom/prometheus
    container_name: monitoring
    ports:
      - "9090:9090"
    volumes:
      - /opt/ai-cluster/config/prometheus.yml:/etc/prometheus/prometheus.yml
    depends_on:
      - ray-head
    restart: unless-stopped

  grafana:
    image: grafana/grafana
    container_name: grafana
    ports:
      - "3000:3000"
    volumes:
      - /opt/ai-cluster/data/grafana:/var/lib/grafana
    depends_on:
      - monitoring
    restart: unless-stopped
EOF

# Create prometheus config
echo "Creating Prometheus configuration..."
mkdir -p /opt/ai-cluster/config
cat > /opt/ai-cluster/config/prometheus.yml << EOF
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'ray'
    static_configs:
      - targets: ['localhost:8265']
EOF

# Create a basic node info file
echo "Creating node information file..."
cat > /opt/ai-cluster/config/node_info.json << EOF
{
    "node_name": "ray-head",
    "node_type": "head",
    "ip_address": "${IP_ADDRESS}",
    "cpu_specs": "Intel i5-7500 4C/4T @ 3.8GHz",
    "ram_specs": "24GB DDR3",
    "resources": {
        "cpu_allocated": ${CPU_LIMIT},
        "memory_allocated": "16GB"
    },
    "tags": ["head", "api", "monitor"]
}
EOF

# Create a simple startup script
echo "Creating startup script..."
cat > /opt/ai-cluster/start-cluster.sh << EOF
#!/bin/bash
cd /opt/ai-cluster
docker-compose up -d
echo "Ray cluster started! Dashboard available at http://${IP_ADDRESS}:${DASHBOARD_PORT}"
EOF
chmod +x /opt/ai-cluster/start-cluster.sh

# Create a simple shutdown script
echo "Creating shutdown script..."
cat > /opt/ai-cluster/stop-cluster.sh << EOF
#!/bin/bash
cd /opt/ai-cluster
docker-compose down
echo "Ray cluster stopped"
EOF
chmod +x /opt/ai-cluster/stop-cluster.sh

# Install Docker and Docker Compose if not already installed
if ! command -v docker &> /dev/null; then
    echo "Installing Docker..."
    # For Arch Linux
    sudo pacman -S --noconfirm docker docker-compose
    sudo systemctl enable docker
    sudo systemctl start docker
    sudo usermod -aG docker $USERNAME
    echo "Docker installed. You may need to log out and back in for group changes to take effect."
fi

echo "Setup complete!"
echo "To start the Ray cluster: /opt/ai-cluster/start-cluster.sh"
echo "To stop the Ray cluster: /opt/ai-cluster/stop-cluster.sh"
echo "Ray dashboard will be available at: http://${IP_ADDRESS}:${DASHBOARD_PORT}"
echo "API Gateway will be available at: http://${IP_ADDRESS}:${API_PORT}"
echo "Grafana will be available at: http://${IP_ADDRESS}:3000"