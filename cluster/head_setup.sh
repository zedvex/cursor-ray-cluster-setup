#!/bin/bash
# Head Node Setup Script for Ray Cluster (NUC)
# Run this on your Ubuntu Desktop NUC to configure it as the Ray head node

set -e  # Exit immediately if a command exits with a non-zero status

echo "========================================================"
echo "Setting up Ray Cluster Head Node on Ubuntu Desktop (NUC)"
echo "========================================================"

# Check if running with sudo privileges
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root or with sudo"
  exit 1
fi

# Get the username of the user who invoked sudo
if [ -n "$SUDO_USER" ]; then
  USERNAME="$SUDO_USER"
else
  # Prompt for username if not run with sudo
  read -p "Enter your username: " USERNAME
fi

echo "Setting up for user: $USERNAME"

# Update package lists (continue even if there are errors with some repositories)
echo "Updating package lists..."
apt-get update || true

# Install required packages
echo "Installing required packages..."
apt-get install -y \
  python3-pip \
  python3-venv \
  python3-dev \
  tmux \
  htop \
  nfs-kernel-server \
  git \
  build-essential \
  libssl-dev \
  openssh-server \
  curl \
  wget \
  apt-transport-https \
  ca-certificates \
  gnupg \
  lsb-release

# Install Docker
echo "Installing Docker..."
if ! command -v docker &> /dev/null; then
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io
  
  # Add user to the docker group so they can run docker without sudo
  usermod -aG docker $USERNAME
  echo "Docker installed."
else
  echo "Docker already installed."
fi

# Install Docker Compose
echo "Installing Docker Compose..."
if ! command -v docker-compose &> /dev/null; then
  DOCKER_COMPOSE_VERSION=$(curl -s https://api.github.com/repos/docker/compose/releases/latest | grep 'tag_name' | cut -d\" -f4)
  curl -L "https://github.com/docker/compose/releases/download/${DOCKER_COMPOSE_VERSION}/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
  chmod +x /usr/local/bin/docker-compose
  echo "Docker Compose installed."
else
  echo "Docker Compose already installed."
fi

# Create directories
echo "Creating required directories..."
mkdir -p /mnt/code
mkdir -p /home/$USERNAME/ray-cluster
mkdir -p /home/$USERNAME/ray-cluster/monitoring
chown -R $USERNAME:$USERNAME /mnt/code
chown -R $USERNAME:$USERNAME /home/$USERNAME/ray-cluster

# Set up NFS exports
echo "Configuring NFS exports..."
if ! grep -q "/mnt/code" /etc/exports; then
  echo "/mnt/code *(rw,sync,no_subtree_check,no_root_squash)" >> /etc/exports
  exportfs -a
  systemctl restart nfs-kernel-server
  echo "NFS server configured."
else
  echo "NFS export already configured."
fi

# Set up Python virtual environment
echo "Setting up Python virtual environment..."
su - $USERNAME -c "python3 -m venv /home/$USERNAME/ray-env"
su - $USERNAME -c "echo 'source ~/ray-env/bin/activate' >> /home/$USERNAME/.bashrc"

# Install Ray and dependencies in the virtual environment
echo "Installing Ray and dependencies..."
su - $USERNAME -c "source /home/$USERNAME/ray-env/bin/activate && pip install --upgrade pip"
su - $USERNAME -c "source /home/$USERNAME/ray-env/bin/activate && pip install 'ray[default]' pandas numpy matplotlib scikit-learn fastapi uvicorn requests psutil"
su - $USERNAME -c "source /home/$USERNAME/ray-env/bin/activate && pip install prometheus-client"

# Optional: Install Anthropic if you'll be using Claude API
su - $USERNAME -c "source /home/$USERNAME/ray-env/bin/activate && pip install anthropic python-dotenv"

# Configure firewall to allow Ray
echo "Configuring firewall..."
ufw allow 22/tcp
ufw allow 6379/tcp      # Ray client server
ufw allow 8265/tcp      # Ray dashboard
ufw allow 8000/tcp      # For FastAPI server
ufw allow 8080/tcp      # For web dashboard
ufw allow 10001/tcp     # Ray internal communication
ufw allow 3000/tcp      # Grafana
ufw allow 9090/tcp      # Prometheus
ufw --force enable

# Create Prometheus configuration file
echo "Creating Prometheus configuration..."
cat > /home/$USERNAME/ray-cluster/monitoring/prometheus.yml << 'EOF'
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'ray'
    static_configs:
      - targets: ['host.docker.internal:8265']
    metrics_path: '/api/metrics'

  - job_name: 'node-exporter'
    static_configs:
      - targets: ['node-exporter:9100']
EOF

# Create a Docker Compose file for monitoring
echo "Creating Docker Compose file for Grafana and Prometheus..."
cat > /home/$USERNAME/ray-cluster/monitoring/docker-compose.yml << 'EOF'
version: '3'

services:
  prometheus:
    image: prom/prometheus:latest
    container_name: prometheus
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
      - prometheus_data:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.path=/prometheus'
      - '--web.console.libraries=/etc/prometheus/console_libraries'
      - '--web.console.templates=/etc/prometheus/consoles'
      - '--web.enable-lifecycle'
      - '--web.external-url=http://localhost:9090'
      - '--web.route-prefix=/'
    restart: unless-stopped
    extra_hosts:
      - "host.docker.internal:host-gateway"

  grafana:
    image: grafana/grafana:latest
    container_name: grafana
    ports:
      - "3000:3000"
    volumes:
      - grafana_data:/var/lib/grafana
      - ./grafana/provisioning:/etc/grafana/provisioning
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
      - GF_USERS_ALLOW_SIGN_UP=false
      - GF_INSTALL_PLUGINS=grafana-piechart-panel
    depends_on:
      - prometheus
    restart: unless-stopped

  node-exporter:
    image: prom/node-exporter:latest
    container_name: node-exporter
    ports:
      - "9100:9100"
    volumes:
      - /proc:/host/proc:ro
      - /sys:/host/sys:ro
      - /:/rootfs:ro
    command:
      - '--path.procfs=/host/proc'
      - '--path.rootfs=/rootfs'
      - '--path.sysfs=/host/sys'
      - '--collector.filesystem.mount-points-exclude=^/(sys|proc|dev|host|etc)($$|/)'
    restart: unless-stopped

volumes:
  prometheus_data:
  grafana_data:
EOF

# Create Grafana provisioning directories and datasource config
mkdir -p /home/$USERNAME/ray-cluster/monitoring/grafana/provisioning/{datasources,dashboards}

# Create Prometheus datasource in Grafana
cat > /home/$USERNAME/ray-cluster/monitoring/grafana/provisioning/datasources/prometheus.yml << 'EOF'
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
EOF

# Create dashboards provisioning
cat > /home/$USERNAME/ray-cluster/monitoring/grafana/provisioning/dashboards/dashboards.yml << 'EOF'
apiVersion: 1

providers:
  - name: 'Ray Cluster Dashboard'
    orgId: 1
    folder: 'Ray'
    type: file
    disableDeletion: false
    updateIntervalSeconds: 30
    options:
      path: /etc/grafana/provisioning/dashboards/json
EOF

# Create dashboard directory for JSON files
mkdir -p /home/$USERNAME/ray-cluster/monitoring/grafana/provisioning/dashboards/json

# Create a basic Ray cluster dashboard
cat > /home/$USERNAME/ray-cluster/monitoring/grafana/provisioning/dashboards/json/ray-cluster-dashboard.json << 'EOF'
{
  "annotations": {
    "list": [
      {
        "builtIn": 1,
        "datasource": "-- Grafana --",
        "enable": true,
        "hide": true,
        "iconColor": "rgba(0, 211, 255, 1)",
        "name": "Annotations & Alerts",
        "type": "dashboard"
      }
    ]
  },
  "editable": true,
  "gnetId": null,
  "graphTooltip": 0,
  "id": 1,
  "links": [],
  "panels": [
    {
      "aliasColors": {},
      "bars": false,
      "dashLength": 10,
      "dashes": false,
      "datasource": "Prometheus",
      "fieldConfig": {
        "defaults": {},
        "overrides": []
      },
      "fill": 1,
      "fillGradient": 0,
      "gridPos": {
        "h": 8,
        "w": 12,
        "x": 0,
        "y": 0
      },
      "hiddenSeries": false,
      "id": 2,
      "legend": {
        "avg": false,
        "current": false,
        "max": false,
        "min": false,
        "show": true,
        "total": false,
        "values": false
      },
      "lines": true,
      "linewidth": 1,
      "nullPointMode": "null",
      "options": {
        "alertThreshold": true
      },
      "percentage": false,
      "pluginVersion": "7.5.6",
      "pointradius": 2,
      "points": false,
      "renderer": "flot",
      "seriesOverrides": [],
      "spaceLength": 10,
      "stack": false,
      "steppedLine": false,
      "targets": [
        {
          "expr": "ray_node_cpu_utilization",
          "interval": "",
          "legendFormat": "{{node}}",
          "refId": "A"
        }
      ],
      "thresholds": [],
      "timeFrom": null,
      "timeRegions": [],
      "timeShift": null,
      "title": "CPU Utilization by Node",
      "tooltip": {
        "shared": true,
        "sort": 0,
        "value_type": "individual"
      },
      "type": "graph",
      "xaxis": {
        "buckets": null,
        "mode": "time",
        "name": null,
        "show": true,
        "values": []
      },
      "yaxes": [
        {
          "format": "percentunit",
          "label": null,
          "logBase": 1,
          "max": "1",
          "min": "0",
          "show": true
        },
        {
          "format": "short",
          "label": null,
          "logBase": 1,
          "max": null,
          "min": null,
          "show": true
        }
      ],
      "yaxis": {
        "align": false,
        "alignLevel": null
      }
    },
    {
      "aliasColors": {},
      "bars": false,
      "dashLength": 10,
      "dashes": false,
      "datasource": "Prometheus",
      "fieldConfig": {
        "defaults": {},
        "overrides": []
      },
      "fill": 1,
      "fillGradient": 0,
      "gridPos": {
        "h": 8,
        "w": 12,
        "x": 12,
        "y": 0
      },
      "hiddenSeries": false,
      "id": 3,
      "legend": {
        "avg": false,
        "current": false,
        "max": false,
        "min": false,
        "show": true,
        "total": false,
        "values": false
      },
      "lines": true,
      "linewidth": 1,
      "nullPointMode": "null",
      "options": {
        "alertThreshold": true
      },
      "percentage": false,
      "pluginVersion": "7.5.6",
      "pointradius": 2,
      "points": false,
      "renderer": "flot",
      "seriesOverrides": [],
      "spaceLength": 10,
      "stack": false,
      "steppedLine": false,
      "targets": [
        {
          "expr": "ray_node_mem_used",
          "interval": "",
          "legendFormat": "{{node}} Used",
          "refId": "A"
        },
        {
          "expr": "ray_node_mem_total",
          "interval": "",
          "legendFormat": "{{node}} Total",
          "refId": "B"
        }
      ],
      "thresholds": [],
      "timeFrom": null,
      "timeRegions": [],
      "timeShift": null,
      "title": "Memory Usage by Node",
      "tooltip": {
        "shared": true,
        "sort": 0,
        "value_type": "individual"
      },
      "type": "graph",
      "xaxis": {
        "buckets": null,
        "mode": "time",
        "name": null,
        "show": true,
        "values": []
      },
      "yaxes": [
        {
          "format": "bytes",
          "label": null,
          "logBase": 1,
          "max": null,
          "min": "0",
          "show": true
        },
        {
          "format": "short",
          "label": null,
          "logBase": 1,
          "max": null,
          "min": null,
          "show": true
        }
      ],
      "yaxis": {
        "align": false,
        "alignLevel": null
      }
    },
    {
      "aliasColors": {},
      "bars": false,
      "dashLength": 10,
      "dashes": false,
      "datasource": "Prometheus",
      "fieldConfig": {
        "defaults": {},
        "overrides": []
      },
      "fill": 1,
      "fillGradient": 0,
      "gridPos": {
        "h": 8,
        "w": 12,
        "x": 0,
        "y": 8
      },
      "hiddenSeries": false,
      "id": 4,
      "legend": {
        "avg": false,
        "current": false,
        "max": false,
        "min": false,
        "show": true,
        "total": false,
        "values": false
      },
      "lines": true,
      "linewidth": 1,
      "nullPointMode": "null",
      "options": {
        "alertThreshold": true
      },
      "percentage": false,
      "pluginVersion": "7.5.6",
      "pointradius": 2,
      "points": false,
      "renderer": "flot",
      "seriesOverrides": [],
      "spaceLength": 10,
      "stack": false,
      "steppedLine": false,
      "targets": [
        {
          "expr": "ray_active_workers",
          "interval": "",
          "legendFormat": "Active Workers",
          "refId": "A"
        },
        {
          "expr": "ray_pending_workers",
          "interval": "",
          "legendFormat": "Pending Workers",
          "refId": "B"
        }
      ],
      "thresholds": [],
      "timeFrom": null,
      "timeRegions": [],
      "timeShift": null,
      "title": "Ray Workers",
      "tooltip": {
        "shared": true,
        "sort": 0,
        "value_type": "individual"
      },
      "type": "graph",
      "xaxis": {
        "buckets": null,
        "mode": "time",
        "name": null,
        "show": true,
        "values": []
      },
      "yaxes": [
        {
          "format": "short",
          "label": null,
          "logBase": 1,
          "max": null,
          "min": "0",
          "show": true
        },
        {
          "format": "short",
          "label": null,
          "logBase": 1,
          "max": null,
          "min": null,
          "show": true
        }
      ],
      "yaxis": {
        "align": false,
        "alignLevel": null
      }
    },
    {
      "aliasColors": {},
      "bars": false,
      "dashLength": 10,
      "dashes": false,
      "datasource": "Prometheus",
      "fieldConfig": {
        "defaults": {},
        "overrides": []
      },
      "fill": 1,
      "fillGradient": 0,
      "gridPos": {
        "h": 8,
        "w": 12,
        "x": 12,
        "y": 8
      },
      "hiddenSeries": false,
      "id": 5,
      "legend": {
        "avg": false,
        "current": false,
        "max": false,
        "min": false,
        "show": true,
        "total": false,
        "values": false
      },
      "lines": true,
      "linewidth": 1,
      "nullPointMode": "null",
      "options": {
        "alertThreshold": true
      },
      "percentage": false,
      "pluginVersion": "7.5.6",
      "pointradius": 2,
      "points": false,
      "renderer": "flot",
      "seriesOverrides": [],
      "spaceLength": 10,
      "stack": false,
      "steppedLine": false,
      "targets": [
        {
          "expr": "ray_tasks_running",
          "interval": "",
          "legendFormat": "Running Tasks",
          "refId": "A"
        },
        {
          "expr": "ray_tasks_waiting",
          "interval": "",
          "legendFormat": "Waiting Tasks",
          "refId": "B"
        }
      ],
      "thresholds": [],
      "timeFrom": null,
      "timeRegions": [],
      "timeShift": null,
      "title": "Ray Tasks",
      "tooltip": {
        "shared": true,
        "sort": 0,
        "value_type": "individual"
      },
      "type": "graph",
      "xaxis": {
        "buckets": null,
        "mode": "time",
        "name": null,
        "show": true,
        "values": []
      },
      "yaxes": [
        {
          "format": "short",
          "label": null,
          "logBase": 1,
          "max": null,
          "min": "0",
          "show": true
        },
        {
          "format": "short",
          "label": null,
          "logBase": 1,
          "max": null,
          "min": null,
          "show": true
        }
      ],
      "yaxis": {
        "align": false,
        "alignLevel": null
      }
    }
  ],
  "refresh": "10s",
  "schemaVersion": 27,
  "style": "dark",
  "tags": ["ray", "cluster"],
  "templating": {
    "list": []
  },
  "time": {
    "from": "now-1h",
    "to": "now"
  },
  "timepicker": {
    "refresh_intervals": [
      "5s",
      "10s",
      "30s",
      "1m",
      "5m",
      "15m",
      "30m",
      "1h",
      "2h",
      "1d"
    ]
  },
  "timezone": "",
  "title": "Ray Cluster Dashboard",
  "uid": "ray-cluster",
  "version": 1
}
EOF

# Set correct permissions
chown -R $USERNAME:$USERNAME /home/$USERNAME/ray-cluster/monitoring

# Create a script to start Ray head node
echo "Creating Ray start script..."
cat > /home/$USERNAME/ray-cluster/start_head.sh << EOF
#!/bin/bash
if [ "\$(id -u)" -eq 0 ]; then
  # If run as root/sudo, execute as the correct user
  echo "Running as root, switching to user $USERNAME..."
  su - $USERNAME -c "source /home/$USERNAME/ray-env/bin/activate && \\
    ray start --head --port=6379 --dashboard-host=0.0.0.0 --include-dashboard=true --dashboard-port=8265 --metrics-export-port=8266"
  echo "Ray head node started. Dashboard available at http://\$(hostname -I | awk '{print \$1}'):8265"
else
  # Run normally as the user
  source ~/ray-env/bin/activate
  ray start --head --port=6379 --dashboard-host=0.0.0.0 --include-dashboard=true --dashboard-port=8265 --metrics-export-port=8266
  echo "Ray head node started. Dashboard available at http://\$(hostname -I | awk '{print \$1}'):8265"
fi
EOF

chmod +x /home/$USERNAME/ray-cluster/start_head.sh
chown $USERNAME:$USERNAME /home/$USERNAME/ray-cluster/start_head.sh

# Create script to stop Ray
cat > /home/$USERNAME/ray-cluster/stop_ray.sh << EOF
#!/bin/bash
if [ "\$(id -u)" -eq 0 ]; then
  # If run as root/sudo, execute as the correct user
  echo "Running as root, switching to user $USERNAME..."
  su - $USERNAME -c "source /home/$USERNAME/ray-env/bin/activate && ray stop"
  echo "Ray stopped"
else
  # Run normally as the user
  source ~/ray-env/bin/activate
  ray stop
  echo "Ray stopped"
fi
EOF

chmod +x /home/$USERNAME/ray-cluster/stop_ray.sh
chown $USERNAME:$USERNAME /home/$USERNAME/ray-cluster/stop_ray.sh

# Create script to start monitoring
cat > /home/$USERNAME/ray-cluster/start_monitoring.sh << EOF
#!/bin/bash
if [ "\$(id -u)" -eq 0 ]; then
  # If run as root/sudo, execute as the correct user
  echo "Running as root, switching to user $USERNAME..."
  su - $USERNAME -c "cd /home/$USERNAME/ray-cluster/monitoring && docker-compose up -d"
  echo "Monitoring started."
  echo "Grafana dashboard available at http://\$(hostname -I | awk '{print \$1}'):3000"
  echo "Default credentials: admin/admin"
else
  # Run normally as the user
  cd ~/ray-cluster/monitoring
  docker-compose up -d
  echo "Monitoring started."
  echo "Grafana dashboard available at http://\$(hostname -I | awk '{print \$1}'):3000"
  echo "Default credentials: admin/admin"
fi
EOF

chmod +x /home/$USERNAME/ray-cluster/start_monitoring.sh
chown $USERNAME:$USERNAME /home/$USERNAME/ray-cluster/start_monitoring.sh

# Create script to stop monitoring
cat > /home/$USERNAME/ray-cluster/stop_monitoring.sh << EOF
#!/bin/bash
if [ "\$(id -u)" -eq 0 ]; then
  # If run as root/sudo, execute as the correct user
  echo "Running as root, switching to user $USERNAME..."
  su - $USERNAME -c "cd /home/$USERNAME/ray-cluster/monitoring && docker-compose down"
  echo "Monitoring stopped."
else
  # Run normally as the user
  cd ~/ray-cluster/monitoring
  docker-compose down
  echo "Monitoring stopped."
fi
EOF

chmod +x /home/$USERNAME/ray-cluster/stop_monitoring.sh
chown $USERNAME:$USERNAME /home/$USERNAME/ray-cluster/stop_monitoring.sh

# Create .env template for Claude API
cat > /home/$USERNAME/ray-cluster/.env.example << 'EOF'
# API Keys
ANTHROPIC_API_KEY=your_anthropic_api_key_here
CLAUDE_MODEL=claude-3-7-sonnet-20250219

# Cluster Configuration
RAY_HEAD_IP=$(hostname -I | awk '{print $1}')
RAY_DASHBOARD_PORT=8265
API_SERVER_PORT=8000
DASHBOARD_PORT=8080
EOF

chown $USERNAME:$USERNAME /home/$USERNAME/ray-cluster/.env.example

# Generate SSH key for the user if it doesn't exist
if [ ! -f "/home/$USERNAME/.ssh/id_rsa" ]; then
  echo "Generating SSH key for passwordless authentication between nodes..."
  su - $USERNAME -c "ssh-keygen -t rsa -N '' -f /home/$USERNAME/.ssh/id_rsa"
  echo "SSH key generated."
  echo "IMPORTANT: You'll need to manually copy this public key to worker nodes:"
  echo "Public key: $(cat /home/$USERNAME/.ssh/id_rsa.pub)"
fi

echo "========================================================"
echo "Ray head node setup completed successfully!"
echo "========================================================"
echo ""
echo "Next steps:"
echo "1. Start Ray head node:      ~/ray-cluster/start_head.sh"
echo "2. Start monitoring stack:   ~/ray-cluster/start_monitoring.sh"
echo "3. Install worker nodes:     Copy and run worker_setup.sh on each worker machine"
echo "4. Customize environment:    Copy .env.example to .env and edit as needed"
echo ""
echo "Ray dashboard will be available at:    http://$(hostname -I | awk '{print $1}'):8265"
echo "Grafana dashboard will be available at: http://$(hostname -I | awk '{print $1}'):3000"
echo "Default Grafana credentials:            admin/admin"
echo "========================================================"