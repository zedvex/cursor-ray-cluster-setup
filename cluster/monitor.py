#!/usr/bin/env python3
"""
Ray Cluster Health Monitoring and Alerting System
Monitors the health of the Ray cluster and sends alerts for failures
"""

import time
import ray
import psutil
import socket
import json
import os
import sys
import signal
import logging
import smtplib
import subprocess
import threading
import argparse
from email.message import EmailMessage
from datetime import datetime
from typing import Dict, List, Any, Optional, Callable
import requests
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Add parent directory to Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Global variables
ALERT_THRESHOLDS = {
    "cpu_percent": 90,
    "memory_percent": 90,
    "disk_percent": 90,
    "node_down_count": 1,
    "response_time_ms": 5000,
}

class ClusterMonitor:
    """Class for monitoring Ray cluster health"""
    
    def __init__(
        self, 
        interval: int = 10, 
        output_file: Optional[str] = None,
        alert_handlers: Optional[List[Callable]] = None,
        thresholds: Optional[Dict[str, float]] = None
    ):
        """
        Initialize the cluster monitor
        
        Args:
            interval: Monitoring interval in seconds
            output_file: Optional file to write monitoring data to
            alert_handlers: List of alert handler functions to call on alerts
            thresholds: Custom thresholds for alerts
        """
        self.interval = interval
        self.output_file = output_file
        self.alert_handlers = alert_handlers or []
        self.thresholds = thresholds or ALERT_THRESHOLDS.copy()
        self.previous_stats = None
        self.running = False
        self.alerts_sent = set()  # Track alerts to avoid duplicates
        
        # Create output directory if needed
        if output_file:
            os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)

    def get_node_stats(self) -> Dict[str, Any]:
        """
        Get statistics for the current node
        
        Returns:
            Dictionary of node statistics
        """
        hostname = socket.gethostname()
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        # Get network stats
        net_io = psutil.net_io_counters()
        
        # Get process count
        process_count = len(psutil.pids())
        
        # Get ray-specific processes
        ray_processes = [p for p in psutil.process_iter(['pid', 'name']) 
                        if 'ray' in p.info['name'].lower()]
        
        # Get load average
        load_avg = os.getloadavg()
        
        return {
            'timestamp': datetime.now().isoformat(),
            'hostname': hostname,
            'ip_address': socket.gethostbyname(hostname),
            'cpu_percent': cpu_percent,
            'memory_percent': memory.percent,
            'disk_percent': disk.percent,
            'memory_available_gb': memory.available / (1024**3),
            'network_bytes_sent': net_io.bytes_sent,
            'network_bytes_recv': net_io.bytes_recv,
            'network_connections': len(psutil.net_connections()),
            'process_count': process_count,
            'ray_process_count': len(ray_processes),
            'load_avg_1min': load_avg[0],
            'load_avg_5min': load_avg[1],
            'load_avg_15min': load_avg[2],
            'uptime_seconds': time.time() - psutil.boot_time()
        }

    def get_cluster_stats(self) -> Dict[str, Any]:
        """
        Get statistics for the entire Ray cluster
        
        Returns:
            Dictionary of cluster statistics
        """
        try:
            nodes = ray.nodes()
            
            # Count alive and dead nodes
            alive_nodes = [node for node in nodes if node['Alive']]
            dead_nodes = [node for node in nodes if not node['Alive']]
            
            # Calculate total and available resources
            total_cpus = sum(node['Resources'].get('CPU', 0) for node in nodes)
            available_cpus = sum(node['Resources'].get('CPU', 0) - 
                               node.get('UsedResources', {}).get('CPU', 0) 
                               for node in nodes)
            
            # Memory calculations (in GB)
            total_memory = sum(node['Resources'].get('memory', 0) 
                             for node in nodes) / (1024**3)
            available_memory = sum(
                (node['Resources'].get('memory', 0) - 
                 node.get('UsedResources', {}).get('memory', 0))
                for node in nodes
            ) / (1024**3)
            
            # GPU calculations if available
            total_gpus = sum(node['Resources'].get('GPU', 0) for node in nodes)
            available_gpus = sum(node['Resources'].get('GPU', 0) - 
                                node.get('UsedResources', {}).get('GPU', 0) 
                                for node in nodes)
            
            # Get tasks statistics from GCS (Global Control Store)
            try:
                # The metrics API is subject to change in future Ray versions
                tasks_running = 0
                tasks_failed = 0
                
                # You may need to adjust this based on your Ray version
                if hasattr(ray, 'cluster_resources'):
                    cluster_info = ray.cluster_resources()
                    # Extract more metrics if available
            except:
                tasks_running = 0
                tasks_failed = 0
            
            return {
                'timestamp': datetime.now().isoformat(),
                'total_nodes': len(nodes),
                'alive_nodes': len(alive_nodes),
                'dead_nodes': len(dead_nodes),
                'total_cpus': total_cpus,
                'available_cpus': available_cpus,
                'cpu_utilization_percent': 
                    (1 - (available_cpus / total_cpus)) * 100 if total_cpus > 0 else 0,
                'total_memory_gb': total_memory,
                'available_memory_gb': available_memory,
                'memory_utilization_percent': 
                    (1 - (available_memory / total_memory)) * 100 if total_memory > 0 else 0,
                'total_gpus': total_gpus,
                'available_gpus': available_gpus,
                'gpu_utilization_percent':
                    (1 - (available_gpus / total_gpus)) * 100 if total_gpus > 0 else 0,
                'tasks_running': tasks_running,
                'tasks_failed': tasks_failed,
                'node_details': [{
                    'node_id': node['NodeID'],
                    'node_ip': node['NodeManagerAddress'],
                    'raylet_pid': node.get('RayletPid'),
                    'hostname': node.get('NodeName', 'unknown'),
                    'alive': node['Alive'],
                    'resources': node['Resources'],
                    'used_resources': node.get('UsedResources', {}),
                    'resource_utilization': {
                        'cpu_percent': (node.get('UsedResources', {}).get('CPU', 0) / 
                                       node['Resources'].get('CPU', 1)) * 100 
                                       if node['Resources'].get('CPU', 0) > 0 else 0,
                        'memory_percent': (node.get('UsedResources', {}).get('memory', 0) / 
                                          node['Resources'].get('memory', 1)) * 100
                                          if node['Resources'].get('memory', 0) > 0 else 0,
                    }
                } for node in nodes]
            }
        except Exception as e:
            logger.error(f"Error getting cluster stats: {str(e)}")
            # Return minimal stats in case of error
            return {
                'timestamp': datetime.now().isoformat(),
                'total_nodes': 0,
                'alive_nodes': 0,
                'dead_nodes': 0,
                'error': str(e),
                'node_details': []
            }

    def check_alerts(
        self, 
        node_stats: Dict[str, Any], 
        cluster_stats: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Check for conditions that should trigger alerts
        
        Args:
            node_stats: Current node statistics
            cluster_stats: Current cluster statistics
            
        Returns:
            List of alert dictionaries
        """
        alerts = []
        
        # Check CPU usage
        if node_stats['cpu_percent'] > self.thresholds['cpu_percent']:
            alerts.append({
                'level': 'warning',
                'type': 'high_cpu',
                'message': f"High CPU usage on {node_stats['hostname']}: {node_stats['cpu_percent']}%",
                'details': {
                    'hostname': node_stats['hostname'],
                    'cpu_percent': node_stats['cpu_percent'],
                    'threshold': self.thresholds['cpu_percent']
                }
            })
        
        # Check memory usage
        if node_stats['memory_percent'] > self.thresholds['memory_percent']:
            alerts.append({
                'level': 'warning',
                'type': 'high_memory',
                'message': f"High memory usage on {node_stats['hostname']}: {node_stats['memory_percent']}%",
                'details': {
                    'hostname': node_stats['hostname'],
                    'memory_percent': node_stats['memory_percent'],
                    'threshold': self.thresholds['memory_percent']
                }
            })
        
        # Check disk usage
        if node_stats['disk_percent'] > self.thresholds['disk_percent']:
            alerts.append({
                'level': 'warning',
                'type': 'high_disk',
                'message': f"High disk usage on {node_stats['hostname']}: {node_stats['disk_percent']}%",
                'details': {
                    'hostname': node_stats['hostname'],
                    'disk_percent': node_stats['disk_percent'],
                    'threshold': self.thresholds['disk_percent']
                }
            })
        
        # Check for dead nodes
        if cluster_stats['dead_nodes'] >= self.thresholds['node_down_count']:
            alerts.append({
                'level': 'critical',
                'type': 'nodes_down',
                'message': f"Critical: {cluster_stats['dead_nodes']} nodes are down",
                'details': {
                    'dead_nodes': cluster_stats['dead_nodes'],
                    'threshold': self.thresholds['node_down_count'],
                    'dead_node_ips': [node['node_ip'] for node in cluster_stats['node_details'] 
                                     if not node['alive']]
                }
            })
        
        # Check if less than 10% of CPUs are available
        if (cluster_stats['available_cpus'] / cluster_stats['total_cpus'] < 0.1 
                if cluster_stats['total_cpus'] > 0 else False):
            alerts.append({
                'level': 'warning',
                'type': 'low_cpu_availability',
                'message': f"Low CPU availability: {cluster_stats['available_cpus']}/{cluster_stats['total_cpus']} CPUs available",
                'details': {
                    'available_cpus': cluster_stats['available_cpus'],
                    'total_cpus': cluster_stats['total_cpus'],
                    'cpu_utilization': cluster_stats['cpu_utilization_percent']
                }
            })
        
        # Check for process changes
        if self.previous_stats:
            # Check for large drop in Ray processes
            prev_ray_processes = self.previous_stats['ray_process_count']
            current_ray_processes = node_stats['ray_process_count']
            
            if prev_ray_processes > 0 and current_ray_processes < prev_ray_processes / 2:
                alerts.append({
                    'level': 'critical',
                    'type': 'ray_process_drop',
                    'message': f"Critical: Ray process count dropped from {prev_ray_processes} to {current_ray_processes}",
                    'details': {
                        'previous_count': prev_ray_processes,
                        'current_count': current_ray_processes,
                        'hostname': node_stats['hostname']
                    }
                })
        
        return alerts

    def send_alerts(self, alerts: List[Dict[str, Any]]) -> None:
        """
        Process and send alerts
        
        Args:
            alerts: List of alert dictionaries
        """
        for alert in alerts:
            # Create a unique ID for this alert to avoid duplicates
            alert_id = f"{alert['type']}:{alert['level']}:{alert['message']}"
            
            # Skip if this exact alert was recently sent
            if alert_id in self.alerts_sent:
                continue
            
            # Add to sent alerts
            self.alerts_sent.add(alert_id)
            
            # Log the alert
            log_method = logger.warning if alert['level'] == 'warning' else logger.error
            log_method(f"ALERT: {alert['message']}")
            
            # Call each alert handler
            for handler in self.alert_handlers:
                try:
                    handler(alert)
                except Exception as e:
                    logger.error(f"Error in alert handler: {str(e)}")
        
        # Clear old alerts (keep for 1 hour / 3600 seconds)
        if time.time() % 3600 < self.interval:
            self.alerts_sent.clear()

    def run(self) -> None:
        """Run the monitoring loop"""
        # Initialize Ray if not already initialized
        if not ray.is_initialized():
            try:
                ray.init(address="auto", ignore_reinit_error=True)
                logger.info(f"Connected to Ray cluster at {ray.util.get_node_ip_address()}")
            except Exception as e:
                logger.error(f"Failed to connect to Ray cluster: {str(e)}")
                logger.info("Starting local Ray instance...")
                ray.init(ignore_reinit_error=True)
        
        self.running = True
        logger.info(f"Starting cluster monitoring (interval: {self.interval}s)")
        
        if self.output_file:
            logger.info(f"Writing monitoring data to {self.output_file}")
        
        try:
            while self.running:
                # Get current stats
                node_stats = self.get_node_stats()
                cluster_stats = self.get_cluster_stats()
                
                # Check for alerts
                alerts = self.check_alerts(node_stats, cluster_stats)
                
                # Send any alerts
                if alerts:
                    self.send_alerts(alerts)
                
                # Print summary to console
                self._print_summary(node_stats, cluster_stats)
                
                # Write to file if specified
                if self.output_file:
                    with open(self.output_file, 'a') as f:
                        f.write(json.dumps({
                            'timestamp': node_stats['timestamp'],
                            'node': node_stats,
                            'cluster': cluster_stats,
                            'alerts': alerts
                        }) + '\n')
                
                # Store for comparison
                self.previous_stats = node_stats
                
                # Sleep until next interval
                time.sleep(self.interval)
                
        except KeyboardInterrupt:
            logger.info("Monitoring stopped by user")
        finally:
            self.running = False
    
    def _print_summary(self, node_stats: Dict[str, Any], cluster_stats: Dict[str, Any]) -> None:
        """
        Print a summary of the current stats to the console
        
        Args:
            node_stats: Current node statistics
            cluster_stats: Current cluster statistics
        """
        print(f"\n--- {datetime.now().isoformat()} ---")
        print(f"Local Node: {node_stats['hostname']} ({node_stats['ip_address']})")
        print(f"CPU: {node_stats['cpu_percent']}% | Memory: {node_stats['memory_percent']}% | Disk: {node_stats['disk_percent']}%")
        print(f"Load Avg: {node_stats['load_avg_1min']:.2f}, {node_stats['load_avg_5min']:.2f}, {node_stats['load_avg_15min']:.2f}")
        
        if 'error' in cluster_stats:
            print(f"Cluster Stats Error: {cluster_stats['error']}")
        else:
            print(f"Cluster: {cluster_stats['alive_nodes']}/{cluster_stats['total_nodes']} nodes alive")
            gpu_info = ''
            if cluster_stats.get('total_gpus', 0) > 0:
                gpu_info = f", {cluster_stats.get('available_gpus', 0)}/{cluster_stats.get('total_gpus', 0)} GPUs"
            
            print(f"Resources: {cluster_stats['available_cpus']:.1f}/{cluster_stats['total_cpus']:.1f} CPUs, "
                  f"{cluster_stats['available_memory_gb']:.1f}/{cluster_stats['total_memory_gb']:.1f} GB RAM{gpu_info}")
            
            # Show node status
            if cluster_stats['node_details']:
                print("\nNode Status:")
                for node in cluster_stats['node_details']:
                    status = "ALIVE" if node['alive'] else "DOWN"
                    host = node.get('hostname', node['node_ip'])
                    cpu_util = node['resource_utilization']['cpu_percent']
                    mem_util = node['resource_utilization']['memory_percent']
                    print(f"  {host}: {status} - CPU: {cpu_util:.1f}%, Memory: {mem_util:.1f}%")
    
    def stop(self) -> None:
        """Stop the monitoring loop"""
        self.running = False


# Email alert handler
def email_alert_handler(
    alert: Dict[str, Any], 
    smtp_server: str, 
    smtp_port: int, 
    sender: str, 
    recipients: List[str],
    username: Optional[str] = None,
    password: Optional[str] = None
) -> None:
    """
    Send an email alert
    
    Args:
        alert: Alert dictionary
        smtp_server: SMTP server address
        smtp_port: SMTP server port
        sender: Sender email address
        recipients: List of recipient email addresses
        username: Optional SMTP username
        password: Optional SMTP password
    """
    # Create email message
    msg = EmailMessage()
    msg['Subject'] = f"Ray Cluster Alert: {alert['level'].upper()} - {alert['type']}"
    msg['From'] = sender
    msg['To'] = ', '.join(recipients)
    
    # Format the message body
    body = f"""
RAY CLUSTER ALERT: {alert['level'].upper()}
    
{alert['message']}

Time: {datetime.now().isoformat()}

Details:
{json.dumps(alert['details'], indent=2)}
"""
    msg.set_content(body)
    
    # Send the email
    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            if username and password:
                server.starttls()
                server.login(username, password)
            server.send_message(msg)
        logger.info(f"Email alert sent to {', '.join(recipients)}")
    except Exception as e:
        logger.error(f"Failed to send email alert: {str(e)}")

# Slack alert handler
def slack_alert_handler(alert: Dict[str, Any], webhook_url: str) -> None:
    """
    Send an alert to Slack
    
    Args:
        alert: Alert dictionary
        webhook_url: Slack webhook URL
    """
    # Set color based on alert level
    color = "#ff0000" if alert['level'] == 'critical' else "#ffcc00"
    
    # Create the message payload
    payload = {
        "attachments": [
            {
                "fallback": alert['message'],
                "color": color,
                "title": f"Ray Cluster Alert: {alert['type']}",
                "text": alert['message'],
                "fields": [
                    {
                        "title": "Level",
                        "value": alert['level'],
                        "short": True
                    },
                    {
                        "title": "Time",
                        "value": datetime.now().isoformat(),
                        "short": True
                    }
                ],
                "footer": "Ray Cluster Monitor"
            }
        ]
    }
    
    # Add fields for each detail
    for key, value in alert['details'].items():
        if isinstance(value, (str, int, float, bool)):
            payload["attachments"][0]["fields"].append({
                "title": key,
                "value": str(value),
                "short": True
            })
    
    # Send the request
    try:
        response = requests.post(webhook_url, json=payload)
        response.raise_for_status()
        logger.info("Slack alert sent successfully")
    except Exception as e:
        logger.error(f"Failed to send Slack alert: {str(e)}")

# Command to restart a node
def restart_node_handler(alert: Dict[str, Any]) -> None:
    """
    Auto-restart if too many Ray processes have died
    
    Args:
        alert: Alert dictionary
    """
    if (alert['level'] == 'critical' and 
        alert['type'] == 'ray_process_drop' and
        alert['details']['current_count'] < 2):
        
        logger.warning("Critical Ray process loss detected, attempting restart")
        
        try:
            # Attempt to restart Ray
            subprocess.run(['ray', 'stop'], check=True)
            time.sleep(2)
            subprocess.run(['ray', 'start'], check=True)
            logger.info("Ray processes restarted successfully")
        except Exception as e:
            logger.error(f"Failed to restart Ray processes: {str(e)}")

# Main function
def main():
    parser = argparse.ArgumentParser(description='Ray Cluster Health Monitoring System')
    parser.add_argument('--interval', type=int, default=10, 
                        help='Monitoring interval in seconds')
    parser.add_argument('--output', type=str, default='./logs/monitor.log', 
                        help='Output file for monitoring data (JSON lines format)')
    parser.add_argument('--email', action='store_true', 
                        help='Enable email alerts')
    parser.add_argument('--email-to', type=str, 
                        help='Email recipients (comma-separated)')
    parser.add_argument('--slack', action='store_true', 
                        help='Enable Slack alerts')
    parser.add_argument('--slack-webhook', type=str, 
                        help='Slack webhook URL')
    parser.add_argument('--auto-restart', action='store_true',
                        help='Enable auto-restart of Ray on critical errors')
    parser.add_argument('--cpu-threshold', type=float, default=90,
                        help='CPU usage threshold percentage')
    parser.add_argument('--memory-threshold', type=float, default=90,
                        help='Memory usage threshold percentage')
    parser.add_argument('--disk-threshold', type=float, default=90,
                        help='Disk usage threshold percentage')
    
    args = parser.parse_args()
    
    # Set up alert handlers
    alert_handlers = []
    
    if args.email and args.email_to:
        # Get email configuration from environment or defaults
        smtp_server = os.environ.get('SMTP_SERVER', 'localhost')
        smtp_port = int(os.environ.get('SMTP_PORT', '25'))
        sender = os.environ.get('EMAIL_SENDER', 'ray-monitor@localhost')
        recipients = [email.strip() for email in args.email_to.split(',')]
        username = os.environ.get('SMTP_USERNAME')
        password = os.environ.get('SMTP_PASSWORD')
        
        # Create email handler
        email_handler = lambda alert: email_alert_handler(
            alert, smtp_server, smtp_port, sender, recipients, username, password)
        alert_handlers.append(email_handler)
    
    if args.slack and args.slack_webhook:
        # Create Slack handler
        slack_handler = lambda alert: slack_alert_handler(alert, args.slack_webhook)
        alert_handlers.append(slack_handler)
    
    if args.auto_restart:
        alert_handlers.append(restart_node_handler)
    
    # Set custom thresholds
    thresholds = ALERT_THRESHOLDS.copy()
    if args.cpu_threshold:
        thresholds['cpu_percent'] = args.cpu_threshold
    if args.memory_threshold:
        thresholds['memory_percent'] = args.memory_threshold
    if args.disk_threshold:
        thresholds['disk_percent'] = args.disk_threshold
    
    # Create and run the monitor
    monitor = ClusterMonitor(
        interval=args.interval, 
        output_file=args.output,
        alert_handlers=alert_handlers,
        thresholds=thresholds
    )
    
    # Handle graceful shutdown
    def signal_handler(sig, frame):
        logger.info("Shutting down monitor...")
        monitor.stop()
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Start monitoring
    monitor.run()

if __name__ == "__main__":
    main()