#!/bin/bash
# Docker-based Ray Cluster Setup
# For Intel i5-7500 @ 3.8GHz with 24GB RAM
# --------------------------------------
# Cleans up previous installation and creates a new Docker-based Ray cluster

set -e  # Exit on any error

# Configuration
HOST_IP="192.168.1.10"  # Your machine's IP on the LAN
RAY_PORT=6379
DASHBOARD_PORT=8265
API_PORT=8000
PORTAINER_PORT=9000
CPU_LIMIT=2
MEMORY_LIMIT=16  # GB
CLUSTER_DIR="/opt/ai-cluster"

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'  # No Color

echo -e "${GREEN}=== Ray Cluster Docker Setup ===${NC}"
echo "Host IP: $HOST_IP"
echo "CPU Allocation: $CPU_LIMIT cores"
echo "Memory Allocation: ${MEMORY_LIMIT}GB"

# Clean up function
cleanup() {
    echo -e "${YELLOW}Cleaning up previous installation...${NC}"
    
    # Stop and remove containers
    if [ -f "$CLUSTER_DIR/docker-compose.yml" ]; then
        cd $CLUSTER_DIR && docker-compose down -v || true
    fi
    
    # Remove containers explicitly (in case docker-compose didn't)
    docker rm -f ray-head api-gateway portainer monitoring grafana 2>/dev/null || true
    
    # Remove previous systemd service if it exists
    sudo systemctl stop ray-cluster.service 2>/dev/null || true
    sudo systemctl disable ray-cluster.service 2>/dev/null || true
    sudo rm -f /etc/systemd/system/ray-cluster.service 2>/dev/null || true
    sudo systemctl daemon-reload 2>/dev/null || true
    
    # Remove previous directory and data
    sudo rm -rf $CLUSTER_DIR
}

# Install Docker if needed
ensure_docker() {
    echo "Checking Docker installation..."
    if ! command -v docker &> /dev/null; then
        echo "Installing Docker..."
        sudo pacman -S --noconfirm docker docker-compose
        sudo systemctl enable docker
        sudo systemctl start docker
        sudo usermod -aG docker $USER
        echo "Docker installed. You may need to log out and back in for group changes to take effect."
        # Add temporary permissions for the current session
        sudo chmod 666 /var/run/docker.sock
    fi
}

# Setup the cluster
setup_cluster() {
    echo "Setting up Ray cluster directory..."
    sudo mkdir -p $CLUSTER_DIR/{config,data,logs,shared,workflows}
    sudo chown -R $USER:$USER $CLUSTER_DIR
    
    echo "Creating docker-compose.yml..."
    cat > $CLUSTER_DIR/docker-compose.yml << EOF
version: '3'

services:
  ray-head:
    image: rayproject/ray:2.9.0
    container_name: ray-head
    hostname: ray-head
    ports:
      - "${RAY_PORT}:${RAY_PORT}"
      - "${DASHBOARD_PORT}:${DASHBOARD_PORT}"
    command: >
      ray start --head
      --port=${RAY_PORT}
      --dashboard-host=0.0.0.0
      --dashboard-port=${DASHBOARD_PORT}
      --num-cpus=${CPU_LIMIT}
      --resources='{"memory": ${MEMORY_LIMIT}000, "head": 1.0}'
      --object-store-memory=${MEMORY_LIMIT}000000000
      --include-dashboard=true
    volumes:
      - ${CLUSTER_DIR}/shared:/root/shared
      - ${CLUSTER_DIR}/logs:/tmp/ray
    environment:
      - RAY_HOST=${HOST_IP}
    network_mode: "host"
    restart: unless-stopped

  api-gateway:
    image: python:3.10-slim
    container_name: api-gateway
    depends_on:
      - ray-head
    volumes:
      - ${CLUSTER_DIR}/api:/app
    working_dir: /app
    network_mode: "host"
    command: >
      bash -c "
        pip install fastapi uvicorn ray[default]>=2.0.0 &&
        echo 'Waiting for Ray to start...' &&
        sleep 15 &&
        echo 'from fastapi import FastAPI
import ray
import os
import time

app = FastAPI(title=\"Ray Cluster API\")

@app.on_event(\"startup\")
async def startup_event():
    # Wait for Ray to be available
    max_retries = 10
    for i in range(max_retries):
        try:
            ray.init(address=\"${HOST_IP}:${RAY_PORT}\", namespace=\"default\", ignore_reinit_error=True)
            break
        except Exception as e:
            if i == max_retries - 1:
                raise
            print(f\"Waiting for Ray to be available... {e}\")
            time.sleep(5)

@app.get(\"/\")
def read_root():
    return {\"status\": \"ok\", \"ray_address\": \"${HOST_IP}:${RAY_PORT}\"}

@app.get(\"/nodes\")
def get_nodes():
    nodes = ray.nodes()
    return {\"nodes\": nodes}
' > /app/main.py &&
        uvicorn main:app --host 0.0.0.0 --port ${API_PORT} --reload"
    restart: unless-stopped

  portainer:
    image: portainer/portainer-ce:latest
    container_name: portainer
    ports:
      - "${PORTAINER_PORT}:9000"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ${CLUSTER_DIR}/data/portainer:/data
    restart: unless-stopped

  monitoring:
    image: prom/prometheus:latest
    container_name: monitoring
    ports:
      - "9090:9090"
    volumes:
      - ${CLUSTER_DIR}/config/prometheus.yml:/etc/prometheus/prometheus.yml
    restart: unless-stopped

  grafana:
    image: grafana/grafana:latest
    container_name: grafana
    ports:
      - "3000:3000"
    volumes:
      - ${CLUSTER_DIR}/data/grafana:/var/lib/grafana
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
      - GF_SECURITY_ADMIN_USER=admin
    restart: unless-stopped
EOF

    echo "Creating Prometheus configuration..."
    mkdir -p $CLUSTER_DIR/config
    cat > $CLUSTER_DIR/config/prometheus.yml << EOF
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'ray'
    static_configs:
      - targets: ['${HOST_IP}:${DASHBOARD_PORT}']
EOF

    echo "Creating API directory..."
    mkdir -p $CLUSTER_DIR/api

    echo "Creating systemd service for automatic startup..."
    sudo bash -c "cat > /etc/systemd/system/ray-cluster.service << EOF
[Unit]
Description=Ray Cluster Docker Compose Service
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${CLUSTER_DIR}
ExecStart=/usr/bin/docker-compose up -d
ExecStop=/usr/bin/docker-compose down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF"

    sudo systemctl daemon-reload
    sudo systemctl enable ray-cluster.service
}

# Create helper scripts
create_helper_scripts() {
    echo "Creating helper scripts..."
    
    # Start script
    cat > $CLUSTER_DIR/start-cluster.sh << EOF
#!/bin/bash
cd $CLUSTER_DIR
docker-compose up -d
echo "Ray cluster started!"
echo "Ray dashboard: http://${HOST_IP}:${DASHBOARD_PORT}"
echo "API Gateway: http://${HOST_IP}:${API_PORT}"
echo "Portainer: http://${HOST_IP}:${PORTAINER_PORT}"
echo "Grafana: http://${HOST_IP}:3000 (admin/admin)"
EOF
    chmod +x $CLUSTER_DIR/start-cluster.sh
    
    # Stop script
    cat > $CLUSTER_DIR/stop-cluster.sh << EOF
#!/bin/bash
cd $CLUSTER_DIR
docker-compose down
echo "Ray cluster stopped"
EOF
    chmod +x $CLUSTER_DIR/stop-cluster.sh
    
    # Status script
    cat > $CLUSTER_DIR/cluster-status.sh << EOF
#!/bin/bash
cd $CLUSTER_DIR
docker-compose ps
echo ""
echo "Container stats:"
docker stats --no-stream ray-head api-gateway
EOF
    chmod +x $CLUSTER_DIR/cluster-status.sh
}

# Main execution
cleanup
ensure_docker
setup_cluster
create_helper_scripts

echo -e "${GREEN}Ray Cluster setup complete!${NC}"
echo "Starting the cluster..."
$CLUSTER_DIR/start-cluster.sh

echo -e "${GREEN}Available services:${NC}"
echo "Ray Dashboard: http://${HOST_IP}:${DASHBOARD_PORT}"
echo "API Gateway: http://${HOST_IP}:${API_PORT}"
echo "Portainer: http://${HOST_IP}:${PORTAINER_PORT} (set admin password on first login)"
echo "Grafana: http://${HOST_IP}:3000 (admin/admin)"
echo ""
echo "Helper scripts:"
echo "- Start cluster: $CLUSTER_DIR/start-cluster.sh"
echo "- Stop cluster: $CLUSTER_DIR/stop-cluster.sh"
echo "- Check status: $CLUSTER_DIR/cluster-status.sh"