#!/bin/bash
#
# Ray Cluster Head Node Setup Script for Arch Linux
# For Intel i5-7500 4C/4T @ 3.8GHz with 24GB RAM
# -------------------------------------------
# This script sets up an Arch Linux machine as a Ray cluster head node
#
# Usage: ./setup_ray_head.sh [--skip-system-update]
#        Run with sudo or as root

set -e  # Exit on any error

# Configuration variables
IP_ADDRESS="192.168.1.10"
NETMASK="24"
GATEWAY="192.168.1.1"
DNS_SERVERS="8.8.8.8 8.8.4.4"
AI_CLUSTER_DIR="/opt/ai-cluster"
RAY_VERSION="2.7.0"
USERNAME=$(logname || echo "${SUDO_USER:-$USER}")
LOG_FILE="/var/log/ray_head_setup.log"
NETWORK_INTERFACE=$(ip -o -4 route show to default | awk '{print $5}' | head -n1)

# Colors for terminal output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Check if running with sudo/root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Please run as root or with sudo.${NC}"
    exit 1
fi

# Create or append to log file
touch "$LOG_FILE"
exec > >(tee -a "$LOG_FILE")
exec 2>&1

echo -e "${BLUE}========================================================${NC}"
echo -e "${BLUE}Ray Cluster Head Node Setup - $(date)${NC}"
echo -e "${BLUE}OS: Arch Linux${NC}"
echo -e "${BLUE}Machine: Intel i5-7500 4C/4T @ 3.8GHz with 24GB RAM${NC}"
echo -e "${BLUE}IP Address: ${IP_ADDRESS}${NC}"
echo -e "${BLUE}========================================================${NC}"

# Function to log steps with timestamps
log() {
    echo -e "[$(date '+%Y-%m-%d %H:%M:%S')] ${GREEN}$1${NC}"
}

# Function to log warnings
warn() {
    echo -e "[$(date '+%Y-%m-%d %H:%M:%S')] ${YELLOW}WARNING: $1${NC}"
}

# Function to log errors
error() {
    echo -e "[$(date '+%Y-%m-%d %H:%M:%S')] ${RED}ERROR: $1${NC}"
}

# Function to check if command exists
command_exists() {
    command -v "$1" &> /dev/null
}

# Parse command line arguments
SKIP_UPDATE=false
for arg in "$@"; do
    case $arg in
        --skip-system-update)
            SKIP_UPDATE=true
            shift
            ;;
    esac
done

# 1. System Update (Arch uses pacman instead of apt)
if [ "$SKIP_UPDATE" = false ]; then
    log "Updating system packages..."
    pacman -Sy || { error "Failed to update package databases"; exit 1; }
    
    log "Upgrading system packages..."
    pacman -Su --noconfirm || { error "Failed to upgrade packages"; exit 1; }
else
    log "Skipping system update as requested"
fi

# 2. Install essential packages (Arch package names)
log "Installing essential packages..."
pacman -S --needed --noconfirm base-devel python python-pip git curl wget htop \
    net-tools sysstat ntp unzip zip jq ufw fail2ban openssh \
    python-virtualenv which || { error "Failed to install essential packages"; exit 1; }

# 3. Set up Python environment and install Ray - Moving this earlier in the process
log "Setting up Python environment..."
mkdir -p ${AI_CLUSTER_DIR}/{config,data,logs,shared,workflows,models,backups,tools,monitoring,api_gateway}
chown -R $USERNAME:$USERNAME ${AI_CLUSTER_DIR}

if [ ! -d "${AI_CLUSTER_DIR}/venv" ]; then
    su - $USERNAME -c "python -m venv ${AI_CLUSTER_DIR}/venv"
fi

log "Installing Ray and dependencies..."
su - $USERNAME -c "source ${AI_CLUSTER_DIR}/venv/bin/activate && \
    pip install --upgrade pip wheel setuptools && \
    pip install 'ray[default,serve,tune]==${RAY_VERSION}' fastapi==0.95.2 uvicorn==0.22.0 \
    requests pandas numpy prometheus-client==0.16.0 pydantic==2.0.3 \
    python-multipart watchdog psutil zeroconf anthropic openai httpx tenacity \
    cachetools python-dotenv"
    
# Verify Ray installation
if ! su - $USERNAME -c "source ${AI_CLUSTER_DIR}/venv/bin/activate && python -c 'import ray; print(ray.__version__)'" > /dev/null; then
    error "Ray installation failed. Please check the logs for errors."
    exit 1
else
    log "Ray ${RAY_VERSION} installed successfully."
fi

# 4. Set up firewall (using ufw which we installed above)
log "Configuring firewall..."
systemctl enable ufw
ufw --force enable
ufw default deny incoming
ufw default allow outgoing
# Ray ports
ufw allow 6379/tcp comment 'Ray head node'
ufw allow 8265/tcp comment 'Ray dashboard'
# SSH, HTTP/HTTPS for APIs and dashboard
ufw allow 22/tcp comment 'SSH'
ufw allow 8000/tcp comment 'API Gateway'
ufw allow 3000/tcp comment 'Grafana'
ufw allow from 192.168.1.0/24 to any comment 'Local network'
ufw status verbose

# 4. Configure static IP (using systemd-networkd in Arch)
log "Configuring static IP address (${IP_ADDRESS}/${NETMASK})..."
mkdir -p /etc/systemd/network/

cat > /etc/systemd/network/20-wired.network << EOF
[Match]
Name=${NETWORK_INTERFACE}

[Network]
Address=${IP_ADDRESS}/${NETMASK}
Gateway=${GATEWAY}
DNS=${DNS_SERVERS}
EOF

log "Enabling systemd-networkd service..."
systemctl enable systemd-networkd
systemctl restart systemd-networkd

log "Enabling systemd-resolved service..."
systemctl enable systemd-resolved
systemctl restart systemd-resolved

# 5. Create directory structure - Moved directory creation up with Python setup
log "Setting up log directories..."
mkdir -p /var/log/ray
chown -R $USERNAME:$USERNAME /var/log/ray

# 6. Configure static IP (using systemd-networkd in Arch)
log "Configuring static IP address (${IP_ADDRESS}/${NETMASK})..."

# 8. Create Ray systemd service
log "Creating Ray head service..."
mkdir -p /etc/systemd/system/
cat > /etc/systemd/system/ray-head.service << EOF
[Unit]
Description=Ray Head Node
After=network.target
Wants=network-online.target
StartLimitIntervalSec=500
StartLimitBurst=5

[Service]
User=${USERNAME}
Group=${USERNAME}
WorkingDirectory=${AI_CLUSTER_DIR}
ExecStart=${AI_CLUSTER_DIR}/venv/bin/ray start --head --port=6379 --dashboard-host=0.0.0.0 --dashboard-port=8265 --num-cpus=2 --resources='{"memory": 16, "head": 1.0}' --include-dashboard=true --object-store-memory=6000000000
Restart=on-failure
RestartSec=5s
StandardOutput=journal
StandardError=journal
SyslogIdentifier=ray-head

[Install]
WantedBy=multi-user.target
EOF

# 9. Create configuration directory and node info
log "Creating cluster configuration..."
cat > ${AI_CLUSTER_DIR}/config/node_info.json << EOF
{
    "node_name": "ray-head",
    "node_type": "head",
    "ip_address": "${IP_ADDRESS}",
    "cpu_specs": "Intel i5-7500 4C/4T @ 3.8GHz",
    "ram_specs": "24GB DDR3",
    "resources": {
        "cpu_allocated": 2,
        "memory_allocated": "16GB"
    },
    "tags": ["head", "api", "monitor"]
}
EOF
chown $USERNAME:$USERNAME ${AI_CLUSTER_DIR}/config/node_info.json

# 10. Create directory for logs
log "Setting up log directories..."
mkdir -p /var/log/ray
chown -R $USERNAME:$USERNAME /var/log/ray

# 11. Create basic monitoring script
log "Creating system monitoring script..."
cat > ${AI_CLUSTER_DIR}/tools/monitor_system.sh << EOF
#!/bin/bash
# Basic system monitoring script

LOG_DIR="${AI_CLUSTER_DIR}/logs"
REPORT_FILE="\${LOG_DIR}/system_report_\$(date +%Y%m%d_%H%M%S).txt"

mkdir -p "\${LOG_DIR}"

echo "System Report - \$(date)" > "\${REPORT_FILE}"
echo "---------------------" >> "\${REPORT_FILE}"
echo "" >> "\${REPORT_FILE}"

echo "CPU Usage:" >> "\${REPORT_FILE}"
if command -v mpstat &> /dev/null; then
    mpstat -P ALL 1 1 >> "\${REPORT_FILE}"
else
    top -bn1 | head -20 >> "\${REPORT_FILE}"
fi
echo "" >> "\${REPORT_FILE}"

echo "Memory Usage:" >> "\${REPORT_FILE}"
free -h >> "\${REPORT_FILE}"
echo "" >> "\${REPORT_FILE}"

echo "Disk Usage:" >> "\${REPORT_FILE}"
df -h >> "\${REPORT_FILE}"
echo "" >> "\${REPORT_FILE}"

echo "Ray Status:" >> "\${REPORT_FILE}"
${AI_CLUSTER_DIR}/venv/bin/ray status >> "\${REPORT_FILE}" 2>&1
echo "" >> "\${REPORT_FILE}"

echo "System report generated at \${REPORT_FILE}"
EOF
chmod +x ${AI_CLUSTER_DIR}/tools/monitor_system.sh
chown $USERNAME:$USERNAME ${AI_CLUSTER_DIR}/tools/monitor_system.sh

# 12. Set up cron job for monitoring
log "Setting up cron job for system monitoring..."
if ! pacman -Q cronie &> /dev/null; then
    log "Installing cronie package for cron functionality..."
    pacman -S --noconfirm cronie
    systemctl enable cronie
    systemctl start cronie
fi

(crontab -u $USERNAME -l 2>/dev/null || echo "") | grep -v "monitor_system.sh" | cat - > /tmp/crontab.tmp
echo "0 * * * * ${AI_CLUSTER_DIR}/tools/monitor_system.sh >/dev/null 2>&1" >> /tmp/crontab.tmp
crontab -u $USERNAME /tmp/crontab.tmp
rm /tmp/crontab.tmp

# 13. Optimize system for Ray
log "Optimizing system for Ray performance..."
# Set vm.overcommit_memory for better memory management
mkdir -p /etc/sysctl.d/
echo "vm.overcommit_memory=1" > /etc/sysctl.d/60-ray-memory.conf
# Set max file descriptors
mkdir -p /etc/security/limits.d/
echo "* soft nofile 65536" > /etc/security/limits.d/ray.conf
echo "* hard nofile 65536" >> /etc/security/limits.d/ray.conf
# Apply sysctl changes
sysctl --system

# 14. Set up a simple watchdog script
log "Creating watchdog script..."
cat > ${AI_CLUSTER_DIR}/tools/watchdog.sh << EOF
#!/bin/bash
# Simple watchdog script to ensure Ray head node is running

RAY_STATUS=\$(systemctl is-active ray-head)

if [ "\$RAY_STATUS" != "active" ]; then
    echo "[WARNING] Ray head node is not active (status: \$RAY_STATUS). Attempting restart..."
    systemctl restart ray-head
    sleep 10
    NEW_STATUS=\$(systemctl is-active ray-head)
    echo "After restart attempt, Ray head status: \$NEW_STATUS"
fi
EOF
chmod +x ${AI_CLUSTER_DIR}/tools/watchdog.sh
chown $USERNAME:$USERNAME ${AI_CLUSTER_DIR}/tools/watchdog.sh

# Add watchdog to crontab
(crontab -u $USERNAME -l 2>/dev/null || echo "") | grep -v "watchdog.sh" | cat - > /tmp/crontab.tmp
echo "*/5 * * * * ${AI_CLUSTER_DIR}/tools/watchdog.sh >> ${AI_CLUSTER_DIR}/logs/watchdog.log 2>&1" >> /tmp/crontab.tmp
crontab -u $USERNAME /tmp/crontab.tmp
rm /tmp/crontab.tmp

# 15. Enable and start Ray service
log "Starting Ray head node service..."
systemctl daemon-reload
systemctl enable ray-head
systemctl start ray-head

# 16. Wait for Ray to start and verify
log "Waiting for Ray to start..."
sleep 10
systemctl status ray-head

# Check ray status
if su - $USERNAME -c "source ${AI_CLUSTER_DIR}/venv/bin/activate && ray status"; then
    log "Ray head node is running successfully"
else
    warn "Ray head node might not be running properly. Check logs with 'journalctl -u ray-head'"
fi

# 17. Create startup summary
log "Creating startup information..."
cat > ${AI_CLUSTER_DIR}/HEAD_NODE_INFO.txt << EOF
=========================================
RAY CLUSTER HEAD NODE INFORMATION
=========================================

IP Address: ${IP_ADDRESS}
Ray Dashboard: http://${IP_ADDRESS}:8265
System: Intel i5-7500 with 24GB RAM

IMPORTANT DIRECTORIES:
- Configuration: ${AI_CLUSTER_DIR}/config
- Logs: ${AI_CLUSTER_DIR}/logs
- Tools: ${AI_CLUSTER_DIR}/tools
- Python env: ${AI_CLUSTER_DIR}/venv

SERVICES:
- Ray Head: systemctl status ray-head

MONITORING:
- Ray Dashboard: http://${IP_ADDRESS}:8265
- System reports: ${AI_CLUSTER_DIR}/logs/system_report_*.txt

NEXT STEPS:
1. Connect worker nodes with:
   ray start --address=${IP_ADDRESS}:6379 --num-cpus=<AVAILABLE_CPUS>

2. Set up remaining components:
   - API Gateway
   - Model Proxy
   - Workflow Packages
   - Monitoring stack

=========================================
EOF
chown $USERNAME:$USERNAME ${AI_CLUSTER_DIR}/HEAD_NODE_INFO.txt

log "Head node setup complete!"
log "Ray dashboard available at: http://${IP_ADDRESS}:8265"
log "See ${AI_CLUSTER_DIR}/HEAD_NODE_INFO.txt for important information"
log "Check service status with: systemctl status ray-head"

# Display IP configuration
ip addr show

echo -e "${BLUE}========================================================${NC}"
echo -e "${GREEN}Ray Head Node Setup Completed Successfully${NC}"
echo -e "${BLUE}========================================================${NC}"