#!/usr/bin/env python3
"""
Resource utilities for Ray cluster - handles optimal resource allocation
based on task type and machine capabilities.
"""

import ray
import psutil
import os
import socket
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

def get_node_resources() -> Dict[str, Any]:
    """
    Get available resources on the current node
    
    Returns:
        Dictionary containing hostname, CPU count, memory, and GPU info
    """
    hostname = socket.gethostname()
    ip_address = socket.gethostbyname(hostname)
    cpu_count = psutil.cpu_count(logical=True)
    memory_gb = psutil.virtual_memory().total / (1024 * 1024 * 1024)
    
    # Check for GPUs if available
    gpu_count = 0
    try:
        import torch
        gpu_count = torch.cuda.device_count()
    except (ImportError, AttributeError):
        # Try another method if torch is not available
        try:
            import subprocess
            result = subprocess.run(['nvidia-smi', '-L'], capture_output=True, text=True)
            if result.returncode == 0:
                gpu_count = len(result.stdout.strip().split('\n'))
        except (subprocess.SubprocessError, FileNotFoundError):
            pass
    
    return {
        "hostname": hostname,
        "ip_address": ip_address,
        "cpu_count": cpu_count,
        "memory_gb": memory_gb,
        "gpu_count": gpu_count
    }

def get_optimal_resource_allocation(
    task_type: str = "default", 
    file_size: Optional[int] = None,
    memory_intensive: bool = False
) -> Dict[str, Any]:
    """
    Determine optimal resource allocation based on task type and input size
    
    Args:
        task_type: Type of task ("default", "cpu_intensive", "memory_intensive", "gpu_required")
        file_size: Size of file to process in bytes (if applicable)
        memory_intensive: Whether the task requires significant memory
        
    Returns:
        Dictionary with resource specifications for Ray
    """
    resources = {}
    
    # Get available system resources
    cpu_count = psutil.cpu_count(logical=True)
    available_memory = psutil.virtual_memory().available
    
    # Default to conservative resource allocation
    if task_type == "cpu_intensive":
        # Use most CPUs but leave some for system
        resources["num_cpus"] = max(1, cpu_count - 1)
    elif task_type == "memory_intensive" or memory_intensive:
        # Allocate up to 70% of available memory
        resources["memory"] = int(available_memory * 0.7)
        resources["num_cpus"] = max(1, cpu_count // 2)  # Use half of CPUs
    elif task_type == "gpu_required":
        resources["num_gpus"] = 1
        resources["num_cpus"] = 1  # Typically pair 1 CPU with 1 GPU
    elif file_size is not None:
        # Scale resources based on file size
        if file_size > 1_000_000_000:  # 1GB
            resources["num_cpus"] = max(2, cpu_count // 2)
            resources["memory"] = int(available_memory * 0.6)  # 60% of available memory
        elif file_size > 100_000_000:  # 100MB
            resources["num_cpus"] = 2
            resources["memory"] = 1 * 1024 * 1024 * 1024  # 1GB
        else:
            resources["num_cpus"] = 1
    else:
        # Default allocation - single CPU
        resources["num_cpus"] = 1
    
    logger.debug(f"Allocated resources for {task_type} task: {resources}")
    return resources

def get_cluster_resources() -> Dict[str, Any]:
    """
    Get total resources available in the Ray cluster
    
    Returns:
        Dictionary with cluster resource information
    """
    if not ray.is_initialized():
        ray.init(address="auto", ignore_reinit_error=True)
        
    nodes = ray.nodes()
    
    total_cpus = sum(node["Resources"].get("CPU", 0) for node in nodes)
    total_gpus = sum(node["Resources"].get("GPU", 0) for node in nodes)
    # Convert from bytes to GB for easier readability
    total_memory = sum(node["Resources"].get("memory", 0) for node in nodes) / (1024**3)
    
    # Get available (unused) resources
    available_cpus = sum(
        node["Resources"].get("CPU", 0) - node.get("UsedResources", {}).get("CPU", 0) 
        for node in nodes
    )
    available_gpus = sum(
        node["Resources"].get("GPU", 0) - node.get("UsedResources", {}).get("GPU", 0) 
        for node in nodes
    )
    available_memory = sum(
        (node["Resources"].get("memory", 0) - node.get("UsedResources", {}).get("memory", 0))
        for node in nodes
    ) / (1024**3)
    
    return {
        "total_nodes": len(nodes),
        "total_cpus": total_cpus,
        "total_gpus": total_gpus,
        "total_memory_gb": total_memory,
        "available_cpus": available_cpus,
        "available_gpus": available_gpus,
        "available_memory_gb": available_memory,
        "nodes": [
            {
                "node_id": node["NodeID"],
                "address": node["NodeManagerAddress"],
                "hostname": node.get("NodeName", "unknown"),
                "cpus": node["Resources"].get("CPU", 0),
                "gpus": node["Resources"].get("GPU", 0),
                "memory_gb": node["Resources"].get("memory", 0) / (1024**3),
                "alive": node["Alive"]
            }
            for node in nodes
        ]
    }

if __name__ == "__main__":
    # If run directly, print node resources
    import json
    print("Local Node Resources:")
    print(json.dumps(get_node_resources(), indent=2))
    
    # Initialize Ray and print cluster resources if available
    try:
        ray.init(address="auto", ignore_reinit_error=True)
        print("\nCluster Resources:")
        print(json.dumps(get_cluster_resources(), indent=2))
    except ConnectionError:
        print("\nCould not connect to Ray cluster. Is it running?")