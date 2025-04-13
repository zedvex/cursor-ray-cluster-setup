#!/usr/bin/env python3
"""
Benchmarking script for measuring performance of various operations on the Ray cluster.

This script provides benchmarks for:
1. Task execution latency
2. Throughput testing
3. Memory/CPU utilization 
4. Data transfer overhead
"""

import argparse
import json
import logging
import os
import random
import sys
import time
from typing import Dict, List, Optional, Tuple, Union, Any

import numpy as np
import psutil
import ray

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("ray-benchmarks")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the benchmarking script."""
    parser = argparse.ArgumentParser(
        description="Run benchmarks on the Ray cluster"
    )
    parser.add_argument(
        "--address", 
        type=str,
        default=None,
        help="The address of the Ray cluster to connect to"
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output file for benchmark results (JSON)"
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=5,
        help="Number of iterations for each benchmark"
    )
    parser.add_argument(
        "--task-count",
        type=int,
        default=1000,
        help="Number of tasks to spawn for throughput testing"
    )
    parser.add_argument(
        "--data-size-mb",
        type=int,
        default=10,
        help="Data size in MB for data transfer benchmark"
    )
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="INFO",
        help="Set the logging level"
    )
    parser.add_argument(
        "--benchmarks",
        type=str,
        default="latency,throughput,resource,data_transfer",
        help="Comma-separated list of benchmarks to run"
    )
    parser.add_argument(
        "--ray-address",
        type=str,
        default="auto",
        help="Ray cluster address (default: auto)",
    )
    parser.add_argument(
        "--payload-size",
        type=int,
        default=1024,
        help="Size of payload in bytes for data transfer benchmarks (default: 1KB)",
    )
    parser.add_argument(
        "--include",
        type=str,
        default="all",
        help="Comma-separated list of benchmarks to run (default: all)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    return parser.parse_args()


@ray.remote
def empty_task() -> float:
    """
    Simple empty task for latency benchmarking.
    
    Returns:
        Current timestamp
    """
    return time.time()


@ray.remote
def compute_task(complexity: int = 1000000) -> Tuple[float, float]:
    """
    CPU-intensive task for computation benchmarking.
    
    Args:
        complexity: Computational complexity factor
        
    Returns:
        Tuple of (result, execution_time)
    """
    start_time = time.time()
    
    # Perform some CPU-intensive calculation
    result = 0
    for i in range(complexity):
        result += i
        
    end_time = time.time()
    return result, end_time - start_time


@ray.remote
def memory_task(size_mb: int = 10) -> Tuple[int, float]:
    """
    Memory-intensive task for memory benchmarking.
    
    Args:
        size_mb: Size of the array to allocate in MB
        
    Returns:
        Tuple of (array_size, execution_time)
    """
    start_time = time.time()
    
    # Allocate memory
    size_bytes = size_mb * 1024 * 1024
    data = bytearray(size_bytes)
    
    # Simple operations on the allocated memory
    for i in range(0, size_bytes, 1024 * 1024):
        data[i] = 1
    
    end_time = time.time()
    return len(data), end_time - start_time


@ray.remote
def data_transfer_task(data: bytes) -> int:
    """
    Task that accepts and returns data for data transfer benchmarking.
    
    Args:
        data: Data to transfer
        
    Returns:
        Size of the data received
    """
    # Perform a small computation on the data
    result = sum(data[::1024 * 1024])
    return len(data)


def benchmark_latency(iterations: int = 5) -> Dict[str, Union[float, List[float]]]:
    """
    Benchmark task execution latency.
    
    Args:
        iterations: Number of iterations to run
        
    Returns:
        Dictionary with latency statistics
    """
    logger.info("Running latency benchmark...")
    
    latencies = []
    
    for i in range(iterations):
        start_time = time.time()
        ray.get(empty_task.remote())
        end_time = time.time()
        
        latency = (end_time - start_time) * 1000  # Convert to ms
        latencies.append(latency)
        
        logger.debug(f"Iteration {i+1}/{iterations}: Latency = {latency:.2f} ms")
    
    # Calculate statistics
    avg_latency = sum(latencies) / len(latencies)
    min_latency = min(latencies)
    max_latency = max(latencies)
    
    logger.info(f"Latency benchmark results: avg={avg_latency:.2f} ms, min={min_latency:.2f} ms, max={max_latency:.2f} ms")
    
    return {
        "avg_latency_ms": avg_latency,
        "min_latency_ms": min_latency,
        "max_latency_ms": max_latency,
        "latencies_ms": latencies,
    }


def benchmark_throughput(task_count: int = 1000, iterations: int = 5) -> Dict[str, Union[float, List[float]]]:
    """
    Benchmark task throughput.
    
    Args:
        task_count: Number of tasks to spawn
        iterations: Number of iterations to run
        
    Returns:
        Dictionary with throughput statistics
    """
    logger.info(f"Running throughput benchmark with {task_count} tasks...")
    
    throughputs = []
    
    for i in range(iterations):
        # Submit tasks
        start_time = time.time()
        tasks = [empty_task.remote() for _ in range(task_count)]
        ray.get(tasks)
        end_time = time.time()
        
        duration = end_time - start_time
        throughput = task_count / duration
        throughputs.append(throughput)
        
        logger.debug(f"Iteration {i+1}/{iterations}: Throughput = {throughput:.2f} tasks/sec, Duration = {duration:.2f} sec")
    
    # Calculate statistics
    avg_throughput = sum(throughputs) / len(throughputs)
    min_throughput = min(throughputs)
    max_throughput = max(throughputs)
    
    logger.info(f"Throughput benchmark results: avg={avg_throughput:.2f} tasks/sec, min={min_throughput:.2f} tasks/sec, max={max_throughput:.2f} tasks/sec")
    
    return {
        "avg_throughput": avg_throughput,
        "min_throughput": min_throughput,
        "max_throughput": max_throughput,
        "throughputs": throughputs,
    }


def benchmark_resource_utilization(complexity: int = 1000000, iterations: int = 5) -> Dict[str, Union[float, List[float]]]:
    """
    Benchmark CPU and memory utilization.
    
    Args:
        complexity: Computational complexity factor
        iterations: Number of iterations to run
        
    Returns:
        Dictionary with resource utilization statistics
    """
    logger.info("Running resource utilization benchmark...")
    
    cpu_times = []
    memory_usages = []
    
    # Get initial process stats
    process = psutil.Process()
    initial_memory = process.memory_info().rss / (1024 * 1024)  # MB
    
    for i in range(iterations):
        # Run CPU-intensive task
        result, exec_time = ray.get(compute_task.remote(complexity))
        cpu_times.append(exec_time)
        
        # Check memory usage
        current_memory = process.memory_info().rss / (1024 * 1024)  # MB
        memory_usage = current_memory - initial_memory
        memory_usages.append(memory_usage)
        
        logger.debug(f"Iteration {i+1}/{iterations}: CPU time = {exec_time:.2f} sec, Memory usage = {memory_usage:.2f} MB")
    
    # Calculate statistics
    avg_cpu_time = sum(cpu_times) / len(cpu_times)
    avg_memory_usage = sum(memory_usages) / len(memory_usages)
    
    logger.info(f"Resource utilization benchmark results: avg_cpu_time={avg_cpu_time:.2f} sec, avg_memory_usage={avg_memory_usage:.2f} MB")
    
    return {
        "avg_cpu_time": avg_cpu_time,
        "cpu_times": cpu_times,
        "avg_memory_usage_mb": avg_memory_usage,
        "memory_usages_mb": memory_usages,
    }


def benchmark_data_transfer(data_size_mb: int = 10, iterations: int = 5) -> Dict[str, Union[float, List[float]]]:
    """
    Benchmark data transfer overhead.
    
    Args:
        data_size_mb: Size of the data to transfer in MB
        iterations: Number of iterations to run
        
    Returns:
        Dictionary with data transfer statistics
    """
    logger.info(f"Running data transfer benchmark with {data_size_mb} MB data...")
    
    transfer_times = []
    
    # Create test data
    data_size = data_size_mb * 1024 * 1024
    data = bytearray(random.getrandbits(8) for _ in range(data_size))
    
    for i in range(iterations):
        # Transfer data to and from a Ray task
        start_time = time.time()
        size = ray.get(data_transfer_task.remote(data))
        end_time = time.time()
        
        transfer_time = end_time - start_time
        transfer_times.append(transfer_time)
        
        logger.debug(f"Iteration {i+1}/{iterations}: Transfer time = {transfer_time:.2f} sec ({data_size_mb} MB)")
    
    # Calculate statistics
    avg_transfer_time = sum(transfer_times) / len(transfer_times)
    min_transfer_time = min(transfer_times)
    max_transfer_time = max(transfer_times)
    
    # Calculate throughput in MB/s
    avg_throughput = data_size_mb / avg_transfer_time
    
    logger.info(f"Data transfer benchmark results: avg_time={avg_transfer_time:.2f} sec, throughput={avg_throughput:.2f} MB/s")
    
    return {
        "data_size_mb": data_size_mb,
        "avg_transfer_time": avg_transfer_time,
        "min_transfer_time": min_transfer_time,
        "max_transfer_time": max_transfer_time,
        "transfer_throughput_mbps": avg_throughput,
        "transfer_times": transfer_times,
    }


def run_benchmarks(args: argparse.Namespace) -> Dict[str, Dict]:
    """
    Run all requested benchmarks.
    
    Args:
        args: Command-line arguments
        
    Returns:
        Dictionary with all benchmark results
    """
    benchmarks_to_run = [b.strip().lower() for b in args.benchmarks.split(",")]
    results = {}
    
    # Run requested benchmarks
    if "latency" in benchmarks_to_run:
        results["latency"] = benchmark_latency(args.iterations)
    
    if "throughput" in benchmarks_to_run:
        results["throughput"] = benchmark_throughput(args.task_count, args.iterations)
    
    if "resource" in benchmarks_to_run:
        results["resource_utilization"] = benchmark_resource_utilization(iterations=args.iterations)
    
    if "data_transfer" in benchmarks_to_run:
        results["data_transfer"] = benchmark_data_transfer(args.data_size_mb, args.iterations)
    
    # Add system info
    results["system_info"] = {
        "cluster_resources": ray.cluster_resources(),
        "available_resources": ray.available_resources(),
        "cpu_count": psutil.cpu_count(),
        "system_memory_gb": psutil.virtual_memory().total / (1024**3),
        "python_version": sys.version,
    }
    
    return results


def main() -> None:
    """Main entry point for the benchmarking script."""
    args = parse_args()
    
    # Set log level
    logger.setLevel(getattr(logging, args.log_level))
    
    logger.info("Starting Ray benchmarks")
    
    # Initialize Ray
    if args.address:
        logger.info(f"Connecting to Ray cluster at {args.address}")
        ray.init(address=args.address)
    else:
        logger.info("Starting local Ray instance")
        ray.init()
    
    try:
        # Run benchmarks
        start_time = time.time()
        results = run_benchmarks(args)
        end_time = time.time()
        
        # Add total execution time
        results["total_execution_time"] = end_time - start_time
        
        # Print summary
        logger.info("Benchmark summary:")
        for benchmark, benchmark_results in results.items():
            if benchmark not in ["system_info", "total_execution_time"]:
                logger.info(f"  {benchmark}: {json.dumps(benchmark_results, indent=2)}")
        
        logger.info(f"Total execution time: {results['total_execution_time']:.2f} seconds")
        
        # Write results to file if requested
        if args.output:
            with open(args.output, "w") as f:
                json.dump(results, f, indent=2)
            logger.info(f"Results written to {args.output}")
        
    except Exception as e:
        logger.error(f"Error running benchmarks: {e}")
        sys.exit(1)
    finally:
        # Shut down Ray
        ray.shutdown()


if __name__ == "__main__":
    main()
