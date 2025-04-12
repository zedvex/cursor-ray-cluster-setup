#!/usr/bin/env python3
"""
Web dashboard for monitoring Ray cluster performance and status.
"""

import os
import sys
import json
import time
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime
import threading
import functools
import psutil
from dotenv import load_dotenv
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import ray

# Add the project root to the Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, project_root)

from ray_tasks.resource_utils import get_cluster_resources, get_node_resources
from ray_tasks.error_handling import error_tracker

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configure server
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8080"))
DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "0.0.0.0")
MONITOR_INTERVAL = int(os.getenv("MONITOR_INTERVAL_SECONDS", "5"))

# Initialize FastAPI
app = FastAPI(
    title="Ray Cluster Dashboard",
    description="Web dashboard for monitoring Ray cluster performance",
    version="1.0.0"
)

# Set up templates and static files
templates_path = os.path.join(os.path.dirname(__file__), "templates")
static_path = os.path.join(os.path.dirname(__file__), "static")

# Create directories if they don't exist
os.makedirs(templates_path, exist_ok=True)
os.makedirs(static_path, exist_ok=True)

# Set up the templates and static files
templates = Jinja2Templates(directory=templates_path)
app.mount("/static", StaticFiles(directory=static_path), name="static")

# In-memory storage for metrics
metrics_store = {
    "cpu_usage": [],
    "memory_usage": [],
    "active_tasks": [],
    "completed_tasks": 0,
    "failed_tasks": 0,
    "error_counts": {},
    "nodes": [],
    "ray_stats": {},
    "last_update": None,
}

# Maximum number of data points to store
MAX_DATA_POINTS = 100

# Create default dashboard.html if it doesn't exist
default_dashboard_html = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Ray Cluster Dashboard</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/css/bootstrap.min.css" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body {
            padding-top: 20px;
            background-color: #f5f5f5;
        }
        .card {
            margin-bottom: 20px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
        }
        .card-header {
            font-weight: bold;
            background-color: #e9ecef;
        }
        .dashboard-header {
            margin-bottom: 30px;
        }
        .dashboard-title {
            font-weight: 300;
        }
        .status-indicator {
            display: inline-block;
            width: 12px;
            height: 12px;
            border-radius: 50%;
            margin-right: 5px;
        }
        .status-healthy {
            background-color: #28a745;
        }
        .status-warning {
            background-color: #ffc107;
        }
        .status-danger {
            background-color: #dc3545;
        }
        .node-card {
            transition: all 0.3s;
        }
        .node-card:hover {
            transform: translateY(-5px);
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="row dashboard-header">
            <div class="col-md-8">
                <h1 class="dashboard-title">Ray Cluster Dashboard</h1>
                <p class="text-muted">
                    Monitoring Ray cluster performance and health
                </p>
            </div>
            <div class="col-md-4 text-end">
                <div class="d-flex align-items-center justify-content-end">
                    <span class="me-2">Cluster Status:</span>
                    <span id="cluster-status">
                        <span class="status-indicator status-warning"></span>
                        Connecting...
                    </span>
                </div>
                <small id="last-updated" class="text-muted">Last updated: Never</small>
            </div>
        </div>
        
        <div class="row">
            <div class="col-md-6">
                <div class="card">
                    <div class="card-header">CPU Usage</div>
                    <div class="card-body">
                        <canvas id="cpu-chart" height="200"></canvas>
                    </div>
                </div>
            </div>
            <div class="col-md-6">
                <div class="card">
                    <div class="card-header">Memory Usage</div>
                    <div class="card-body">
                        <canvas id="memory-chart" height="200"></canvas>
                    </div>
                </div>
            </div>
        </div>
        
        <div class="row">
            <div class="col-md-6">
                <div class="card">
                    <div class="card-header">Task Statistics</div>
                    <div class="card-body">
                        <div class="row">
                            <div class="col-md-4 text-center">
                                <h4 id="active-tasks">0</h4>
                                <p class="text-muted">Active Tasks</p>
                            </div>
                            <div class="col-md-4 text-center">
                                <h4 id="completed-tasks">0</h4>
                                <p class="text-muted">Completed Tasks</p>
                            </div>
                            <div class="col-md-4 text-center">
                                <h4 id="failed-tasks">0</h4>
                                <p class="text-muted">Failed Tasks</p>
                            </div>
                        </div>
                        <div class="mt-3">
                            <canvas id="tasks-chart" height="200"></canvas>
                        </div>
                    </div>
                </div>
            </div>
            <div class="col-md-6">
                <div class="card">
                    <div class="card-header">Error Distribution</div>
                    <div class="card-body">
                        <canvas id="error-chart" height="200"></canvas>
                        <div id="no-errors-message" class="text-center mt-5 d-none">
                            <p class="text-muted">No errors recorded</p>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        
        <div class="card">
            <div class="card-header">Cluster Nodes</div>
            <div class="card-body">
                <div id="nodes-container" class="row">
                    <div class="col-12 text-center py-5">
                        <div class="spinner-border text-primary" role="status">
                            <span class="visually-hidden">Loading...</span>
                        </div>
                        <p class="mt-2">Loading cluster nodes...</p>
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        // Chart configuration
        const cpuChart = new Chart(document.getElementById('cpu-chart').getContext('2d'), {
            type: 'line',
            data: {
                labels: [],
                datasets: [{
                    label: 'CPU Usage (%)',
                    data: [],
                    borderColor: '#0d6efd',
                    backgroundColor: 'rgba(13, 110, 253, 0.1)',
                    borderWidth: 2,
                    fill: true,
                    tension: 0.2
                }]
            },
            options: {
                scales: {
                    y: {
                        beginAtZero: true,
                        max: 100,
                        title: {
                            display: true,
                            text: 'Percent'
                        }
                    },
                    x: {
                        title: {
                            display: true,
                            text: 'Time'
                        }
                    }
                },
                animation: false,
                plugins: {
                    legend: {
                        display: true
                    }
                }
            }
        });
        
        const memoryChart = new Chart(document.getElementById('memory-chart').getContext('2d'), {
            type: 'line',
            data: {
                labels: [],
                datasets: [{
                    label: 'Memory Usage (%)',
                    data: [],
                    borderColor: '#dc3545',
                    backgroundColor: 'rgba(220, 53, 69, 0.1)',
                    borderWidth: 2,
                    fill: true,
                    tension: 0.2
                }]
            },
            options: {
                scales: {
                    y: {
                        beginAtZero: true,
                        max: 100,
                        title: {
                            display: true,
                            text: 'Percent'
                        }
                    },
                    x: {
                        title: {
                            display: true,
                            text: 'Time'
                        }
                    }
                },
                animation: false,
                plugins: {
                    legend: {
                        display: true
                    }
                }
            }
        });
        
        const tasksChart = new Chart(document.getElementById('tasks-chart').getContext('2d'), {
            type: 'line',
            data: {
                labels: [],
                datasets: [{
                    label: 'Active Tasks',
                    data: [],
                    borderColor: '#0d6efd',
                    backgroundColor: 'rgba(13, 110, 253, 0.1)',
                    borderWidth: 2,
                    fill: true,
                    tension: 0.2
                }]
            },
            options: {
                scales: {
                    y: {
                        beginAtZero: true,
                        title: {
                            display: true,
                            text: 'Count'
                        }
                    },
                    x: {
                        title: {
                            display: true,
                            text: 'Time'
                        }
                    }
                },
                animation: false,
                plugins: {
                    legend: {
                        display: true
                    }
                }
            }
        });
        
        const errorChart = new Chart(document.getElementById('error-chart').getContext('2d'), {
            type: 'doughnut',
            data: {
                labels: [],
                datasets: [{
                    data: [],
                    backgroundColor: [
                        '#dc3545',
                        '#ffc107',
                        '#6f42c1',
                        '#fd7e14',
                        '#20c997'
                    ],
                    borderWidth: 1
                }]
            },
            options: {
                plugins: {
                    legend: {
                        position: 'right'
                    }
                }
            }
        });
        
        // Function to update dashboard with new data
        function updateDashboard() {
            fetch('/api/metrics')
                .then(response => response.json())
                .then(data => {
                    // Update last updated time
                    const lastUpdated = new Date(data.last_update * 1000);
                    document.getElementById('last-updated').textContent = 
                        `Last updated: ${lastUpdated.toLocaleTimeString()}`;
                    
                    // Update cluster status
                    const clusterStatus = document.getElementById('cluster-status');
                    if (data.ray_stats.is_connected) {
                        if (data.ray_stats.total_nodes > 0) {
                            clusterStatus.innerHTML = `
                                <span class="status-indicator status-healthy"></span>
                                Healthy (${data.ray_stats.total_nodes} nodes)
                            `;
                        } else {
                            clusterStatus.innerHTML = `
                                <span class="status-indicator status-warning"></span>
                                Warning (No worker nodes)
                            `;
                        }
                    } else {
                        clusterStatus.innerHTML = `
                            <span class="status-indicator status-danger"></span>
                            Disconnected
                        `;
                    }
                    
                    // Update task counters
                    document.getElementById('active-tasks').textContent = 
                        data.active_tasks.length > 0 ? data.active_tasks[data.active_tasks.length - 1] : 0;
                    document.getElementById('completed-tasks').textContent = data.completed_tasks;
                    document.getElementById('failed-tasks').textContent = data.failed_tasks;
                    
                    // Update charts
                    updateChart(cpuChart, data.cpu_usage);
                    updateChart(memoryChart, data.memory_usage);
                    updateChart(tasksChart, data.active_tasks);
                    
                    // Update error chart
                    if (Object.keys(data.error_counts).length > 0) {
                        document.getElementById('no-errors-message').classList.add('d-none');
                        updateErrorChart(errorChart, data.error_counts);
                    } else {
                        document.getElementById('no-errors-message').classList.remove('d-none');
                        errorChart.data.labels = [];
                        errorChart.data.datasets[0].data = [];
                        errorChart.update();
                    }
                    
                    // Update nodes
                    updateNodes(data.nodes);
                })
                .catch(error => {
                    console.error('Error fetching metrics:', error);
                    document.getElementById('cluster-status').innerHTML = `
                        <span class="status-indicator status-danger"></span>
                        Connection Error
                    `;
                });
        }
        
        // Helper function to update line charts
        function updateChart(chart, dataPoints) {
            if (!dataPoints || dataPoints.length === 0) return;
            
            // Create labels (last N timestamps)
            const labels = dataPoints.map((_, i) => {
                const time = new Date();
                time.setSeconds(time.getSeconds() - (dataPoints.length - 1 - i) * 5);
                return time.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit', second:'2-digit'});
            });
            
            chart.data.labels = labels;
            chart.data.datasets[0].data = dataPoints;
            chart.update();
        }
        
        // Helper function to update error chart
        function updateErrorChart(chart, errorCounts) {
            const labels = Object.keys(errorCounts);
            const data = Object.values(errorCounts);
            
            chart.data.labels = labels;
            chart.data.datasets[0].data = data;
            chart.update();
        }
        
        // Helper function to update nodes display
        function updateNodes(nodes) {
            if (!nodes || nodes.length === 0) return;
            
            const nodesContainer = document.getElementById('nodes-container');
            
            // Clear previous content
            nodesContainer.innerHTML = '';
            
            // Add node cards
            nodes.forEach(node => {
                const nodeCard = document.createElement('div');
                nodeCard.className = 'col-md-4 mb-4';
                nodeCard.innerHTML = `
                    <div class="card node-card h-100">
                        <div class="card-header d-flex justify-content-between align-items-center">
                            <span>${node.hostname}</span>
                            <span class="status-indicator ${node.alive ? 'status-healthy' : 'status-danger'}"></span>
                        </div>
                        <div class="card-body">
                            <div class="mb-2">
                                <small class="text-muted">Address:</small>
                                <div>${node.address}</div>
                            </div>
                            <div class="mb-2">
                                <small class="text-muted">Resources:</small>
                                <div>
                                    CPU: ${node.cpus.toFixed(1)} | 
                                    Memory: ${node.memory_gb.toFixed(2)} GB
                                    ${node.gpus ? ` | GPU: ${node.gpus}` : ''}
                                </div>
                            </div>
                            <div class="progress mt-3" style="height: 5px;">
                                <div class="progress-bar" role="progressbar" 
                                     style="width: ${node.cpu_used_percent}%;" 
                                     aria-valuenow="${node.cpu_used_percent}" 
                                     aria-valuemin="0" 
                                     aria-valuemax="100">
                                </div>
                            </div>
                            <small class="text-muted">CPU: ${node.cpu_used_percent.toFixed(1)}%</small>
                            
                            <div class="progress mt-2" style="height: 5px;">
                                <div class="progress-bar bg-danger" role="progressbar" 
                                     style="width: ${node.memory_used_percent}%;" 
                                     aria-valuenow="${node.memory_used_percent}" 
                                     aria-valuemin="0" 
                                     aria-valuemax="100">
                                </div>
                            </div>
                            <small class="text-muted">Memory: ${node.memory_used_percent.toFixed(1)}%</small>
                        </div>
                    </div>
                `;
                nodesContainer.appendChild(nodeCard);
            });
        }
        
        // Initial update and periodic refresh
        updateDashboard();
        setInterval(updateDashboard, 5000);
    </script>
</body>
</html>
"""

template_file = os.path.join(templates_path, "dashboard.html")
if not os.path.isfile(template_file):
    with open(template_file, 'w', encoding='utf-8') as f:
        f.write(default_dashboard_html)

# Initialize Ray when the server starts
@app.on_event("startup")
async def startup_event():
    logger.info("Starting Ray Cluster Dashboard")
    
    try:
        ray.init(address="auto", ignore_reinit_error=True)
        logger.info("Connected to existing Ray cluster")
        
        # Start the metrics collection thread
        threading.Thread(target=collect_metrics, daemon=True).start()
                    
    except ConnectionError:
        logger.warning("Could not connect to Ray cluster, initializing local Ray instance")
        ray.init(ignore_reinit_error=True)
        
        # Start the metrics collection thread anyway
        threading.Thread(target=collect_metrics, daemon=True).start()

# Shutdown Ray when the server stops
@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down Ray Cluster Dashboard")
    ray.shutdown()

# Function to collect metrics periodically
def collect_metrics():
    """Collect metrics from Ray cluster and update metrics store."""
    global metrics_store
    
    while True:
        try:
            # Get local node resources
            local_resources = get_node_resources()
            
            # Get Ray cluster resources if connected
            try:
                cluster_resources = get_cluster_resources()
                is_connected = True
                
                # Update node information
                nodes = []
                for node in cluster_resources.get("nodes", []):
                    # Calculate usage percentages
                    cpu_used = node.get("cpus", 0) - cluster_resources.get("available_cpus", 0) / len(cluster_resources.get("nodes", []))
                    memory_used = node.get("memory_gb", 0) - cluster_resources.get("available_memory_gb", 0) / len(cluster_resources.get("nodes", []))
                    
                    cpu_used_percent = (cpu_used / node.get("cpus", 1)) * 100 if node.get("cpus", 0) > 0 else 0
                    memory_used_percent = (memory_used / node.get("memory_gb", 1)) * 100 if node.get("memory_gb", 0) > 0 else 0
                    
                    nodes.append({
                        "hostname": node.get("hostname", "unknown"),
                        "address": node.get("address", "unknown"),
                        "cpus": node.get("cpus", 0),
                        "memory_gb": node.get("memory_gb", 0),
                        "gpus": node.get("gpus", 0),
                        "alive": node.get("alive", False),
                        "cpu_used_percent": min(100, max(0, cpu_used_percent)),
                        "memory_used_percent": min(100, max(0, memory_used_percent))
                    })
                
                # Get current running tasks (simplified)
                active_tasks = len(ray.nodes())  # This is just a proxy, in reality we would track tasks
                
                # Update metrics
                metrics_store["cpu_usage"].append(psutil.cpu_percent())
                metrics_store["memory_usage"].append(psutil.virtual_memory().percent)
                metrics_store["active_tasks"].append(active_tasks)
                metrics_store["nodes"] = nodes
                metrics_store["ray_stats"] = {
                    "is_connected": is_connected,
                    "total_nodes": len(nodes),
                    "total_cpus": cluster_resources.get("total_cpus", 0),
                    "total_memory_gb": cluster_resources.get("total_memory_gb", 0),
                    "available_cpus": cluster_resources.get("available_cpus", 0),
                    "available_memory_gb": cluster_resources.get("available_memory_gb", 0),
                }
                
                # Get error statistics from the global error tracker if available
                try:
                    if hasattr(error_tracker, "get_report"):
                        error_report = error_tracker.get_report()
                        metrics_store["completed_tasks"] = error_report.get("total_tasks", 0) - error_report.get("failed_tasks", 0)
                        metrics_store["failed_tasks"] = error_report.get("failed_tasks", 0)
                        
                        # Extract error counts by type
                        error_types = error_report.get("error_types", {})
                        metrics_store["error_counts"] = {
                            error_type: data.get("count", 0)
                            for error_type, data in error_types.items()
                        }
                except Exception as e:
                    logger.warning(f"Error getting error statistics: {str(e)}")
                
            except ConnectionError:
                # Handle Ray cluster not available
                is_connected = False
                metrics_store["ray_stats"] = {"is_connected": False}
                metrics_store["nodes"] = []
                
                # Still track local metrics
                metrics_store["cpu_usage"].append(psutil.cpu_percent())
                metrics_store["memory_usage"].append(psutil.virtual_memory().percent)
                metrics_store["active_tasks"].append(0)
            
            # Limit the number of data points
            for key in ["cpu_usage", "memory_usage", "active_tasks"]:
                if len(metrics_store[key]) > MAX_DATA_POINTS:
                    metrics_store[key] = metrics_store[key][-MAX_DATA_POINTS:]
            
            # Update timestamp
            metrics_store["last_update"] = time.time()
            
        except Exception as e:
            logger.error(f"Error collecting metrics: {str(e)}")
        
        # Sleep for the specified interval
        time.sleep(MONITOR_INTERVAL)

# API endpoint to get metrics data
@app.get("/api/metrics")
async def get_metrics():
    """Return current metrics data."""
    return metrics_store

# Main dashboard route
@app.get("/", response_class=HTMLResponse)
async def get_dashboard(request: Request):
    """Render the dashboard template."""
    return templates.TemplateResponse(
        "dashboard.html", 
        {"request": request}
    )

# Health check endpoint
@app.get("/health")
async def health_check():
    """Check if the dashboard is running."""
    is_ray_connected = False
    try:
        if ray.is_initialized():
            is_ray_connected = True
    except:
        pass
    
    return {
        "status": "healthy",
        "ray_connected": is_ray_connected,
        "timestamp": time.time()
    }

# Main entry point
def main():
    try:
        logger.info(f"Starting dashboard on http://{DASHBOARD_HOST}:{DASHBOARD_PORT}")
        uvicorn.run(
            "web.dashboard:app", 
            host=DASHBOARD_HOST,
            port=DASHBOARD_PORT,
            reload=False
        )
    except KeyboardInterrupt:
        logger.info("Dashboard stopped by user")
    except Exception as e:
        logger.error(f"Error starting dashboard: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
