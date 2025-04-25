#!/bin/bash
# Cleanup script for Ray head node installation

echo "Cleaning up Ray head node installation..."

# Stop and disable services
sudo systemctl stop ray-head 2>/dev/null
sudo systemctl disable ray-head 2>/dev/null

# Remove systemd service files
sudo rm -f /etc/systemd/system/ray-head.service
sudo systemctl daemon-reload

# Remove network configuration (if changed)
sudo rm -f /etc/systemd/network/20-wired.network

# Remove directories and files created
sudo rm -rf /opt/ai-cluster
sudo rm -rf /var/log/ray

# Remove cron jobs
(crontab -l 2>/dev/null | grep -v "monitor_system.sh" | grep -v "watchdog.sh") | crontab -

echo "Cleanup complete. You may need to reconfigure your network settings manually."