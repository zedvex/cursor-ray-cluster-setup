#!/usr/bin/env python3
"""
Benchmarking script for Ray cluster - measures performance for different task types,
batch sizes, and workloads to help optimize cluster configuration.
"""

import os
import sys
import time
import argparse
import logging
import random
import json
import math
import statistics
from typing import List, Dict, Any, Optional, Callable, Tuple
import ray

# Add the project root to the Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, project_root)

from ray_tasks.task_manager import distribute_tasks, execute_in_parallel
from ray_tasks.resource_utils import get_cluster_resources, get_node_resources
from ray_tasks.error_handling import retry

# Configure logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ---------- CPU Benchmarks ----------

@ray.remote
def cpu_intensive_task(n: int = 10000000) -> Dict[str, Any]:
    """
    CPU-intensive benchmark that computes prime numbers
    
    Args:
        n: Upper limit for finding prime numbers
        
    Returns:
        Dictionary with benchmark results
    """
    start_time = time.time()
    
    # Get node info
    import socket
    node_name = socket.gethostname()
    
    # Simple prime number computation (intentionally inefficient)
    primes = []
    for i in range(2, n):
        is_prime = True
        for j in range(2, int(math.sqrt(i)) + 1):
            if i % j == 0:
                is_prime = False
                break
        if is_prime:
            primes.append(i)
    
    # Take only the last 10 primes to keep result size small
    result_primes = primes[-10:] if primes else []
    elapsed_time = time.time() - start_time
    
    return {
        "task_type": "cpu_intensive",
        "node": node_name,
        "input_size": n,
        "elapsed_time": elapsed_time,
        "result_sample": result_primes
    }

# ---------- Memory Benchmarks ----------

@ray.remote
def memory_intensive_task(size_mb: int = 100) -> Dict[str, Any]:
    """
    Memory-intensive benchmark that allocates and processes large arrays
    
    Args:
        size_mb: Size of memory to allocate in MB
        
    Returns:
        Dictionary with benchmark results
    """
    start_time = time.time()
    
    # Get node info
    import socket
    node_name = socket.gethostname()
    
    # Allocate a large array
    try:
        import numpy as np
        # Create a large array (size_mb megabytes)
        array_size = size_mb * 1024 * 1024 // 8  # Convert MB to number of float64 elements
        data = np.random.random(array_size)
        
        # Do some computation on the array
        result = np.mean(data), np.std(data), np.min(data), np.max(data)
        success = True
    except Exception as e:
        result = str(e)
        success = False
    
    elapsed_time = time.time() - start_time
    
    return {
        "task_type": "memory_intensive",
        "node": node_name,
        "memory_mb": size_mb,
        "elapsed_time": elapsed_time,
        "success": success,
        "result_sample": result
    }

# ---------- I/O Benchmarks ----------

@ray.remote
def io_intensive_task(file_size_mb: int = 10, read_only: bool = False) -> Dict[str, Any]:
    """
    I/O-intensive benchmark that writes and reads temporary files
    
    Args:
        file_size_mb: Size of the temporary file to create in MB
        read_only: If True, only read an existing file, don't write
        
    Returns:
        Dictionary with benchmark results
    """
    import os
    import tempfile
    import hashlib
    
    start_time = time.time()
    
    # Get node info
    import socket
    node_name = socket.gethostname()
    
    # Create a temporary file
    temp_file = None
    try:
        with tempfile.NamedTemporaryFile(delete=False) as temp:
            temp_file = temp.name
            
            # Write phase
            write_time = 0
            if not read_only:
                write_start = time.time()
                # Write random data to the file
                chunk_size = 1024 * 1024  # 1MB
                for _ in range(file_size_mb):
                    temp.write(os.urandom(chunk_size))
                temp.flush()
                os.fsync(temp.fileno())  # Make sure it's written to disk
                write_time = time.time() - write_start
            
            # Read phase
            read_start = time.time()
            # Read the file and compute hash
            md5 = hashlib.md5()
            with open(temp_file, 'rb') as f:
                while True:
                    data = f.read(1024 * 1024)
                    if not data:
                        break
                    md5.update(data)
            read_time = time.time() - read_start
            
            success = True
            result = {
                "file_size_mb": file_size_mb,
                "md5": md5.hexdigest(),
                "write_time": write_time,
                "read_time": read_time
            }
    except Exception as e:
        success = False
        result = str(e)
    finally:
        # Clean up
        if temp_file and os.path.exists(temp_file):
            os.unlink(temp_file)
    
    elapsed_time = time.time() - start_time
    
    return {
        "task_type": "io_intensive",
        "node": node_name,
        "elapsed_time": elapsed_time,
        "success": success,
        "result": result
    }

# ---------- Network Benchmarks ----------

@ray.remote
def ping_task(target_node: str, data_size_kb: int = 10) -> Dict[str, Any]:
    """
    Network benchmark that measures inter-node communication
    
    Args:
        target_node: Node to ping
        data_size_kb: Size of data to send in KB
        
    Returns:
        Dictionary with benchmark results
    """
    start_time = time.time()
    
    # Get node info
    import socket
    source_node = socket.gethostname()
    
    # Create random data to send
    data = os.urandom(data_size_kb * 1024)
    
    try:
        # We'll ping by submitting a task to the target node
        # and waiting for the response
        @ray.remote
        def echo_task(data, source):
            import socket
            return {
                "data_size": len(data),
                "source": source,
                "target": socket.gethostname(),
                "received_at": time.time()
            }
        
        # Get available nodes
        nodes = ray.nodes()
        target_found = False
        
        for node in nodes:
            if target_node in node.get("NodeManagerAddress", "") or target_node == node.get("NodeName"):
                target_found = True
                # Place the echo task on the target node
                node_ip = node["NodeManagerAddress"]
                remote_task = echo_task.options(resources={f"node:{node_ip}": 0.01}).remote(data, source_node)
                # Wait for the result
                response = ray.get(remote_task)
                success = True
                break
        
        if not target_found:
            response = f"Target node {target_node} not found"
            success = False
            
    except Exception as e:
        response = str(e)
        success = False
    
    elapsed_time = time.time() - start_time
    
    return {
        "task_type": "network",
        "source_node": source_node,
        "target_node": target_node,
        "data_size_kb": data_size_kb,
        "elapsed_time": elapsed_time,
        "success": success,
        "result": response
    }

# ---------- Batch Processing Benchmarks ----------

@ray.remote
def process_batch(batch: List[int], work_factor: int = 1000000) -> Dict[str, Any]:
    """
    Process a batch of items with configurable workload
    
    Args:
        batch: List of items to process
        work_factor: Factor controlling the amount of work per item
        
    Returns:
        Dictionary with benchmark results
    """
    start_time = time.time()
    
    # Get node info
    import socket
    node_name = socket.gethostname()
    
    results = []
    for item in batch:
        # Simulate some CPU-intensive work
        result = 0
        for i in range(work_factor):
            result += math.sin(item + i)
        results.append(result)
    
    elapsed_time = time.time() - start_time
    
    return {
        "task_type": "batch",
        "node": node_name,
        "batch_size": len(batch),
        "work_factor": work_factor,
        "elapsed_time": elapsed_time,
        "results_sample": results[:3] if results else []
    }

# ---------- Benchmark Runners ----------

def run_cpu_benchmark(
    repetitions: int = 5,
    workload_sizes: Optional[List[int]] = None,
    num_tasks: int = 10
) -> Dict[str, Any]:
    """
    Run CPU-intensive benchmark
    
    Args:
        repetitions: Number of times to repeat each workload
        workload_sizes: List of workload sizes to test
        num_tasks: Number of tasks to run for each workload size
        
    Returns:
        Dictionary with benchmark results
    """
    if workload_sizes is None:
        workload_sizes = [1000000, 5000000, 10000000]
    
    logger.info("Running CPU benchmark")
    
    results = {}
    
    for size in workload_sizes:
        logger.info(f"Testing workload size: {size}")
        
        size_results = []
        for rep in range(repetitions):
            logger.info(f"Repetition {rep+1}/{repetitions}")
            
            # Create tasks
            tasks = [cpu_intensive_task.remote(size) for _ in range(num_tasks)]
            
            # Measure total time
            start_time = time.time()
            task_results = ray.get(tasks)
            total_time = time.time() - start_time
            
            # Calculate task times
            task_times = [result["elapsed_time"] for result in task_results]
            
            # Record results
            rep_result = {
                "total_time": total_time,
                "task_times": task_times,
                "avg_task_time": statistics.mean(task_times),
                "min_task_time": min(task_times),
                "max_task_time": max(task_times),
                "std_dev": statistics.stdev(task_times) if len(task_times) > 1 else 0,
                "tasks_per_second": num_tasks / total_time
            }
            size_results.append(rep_result)
        
        # Aggregate results across repetitions
        avg_total_time = statistics.mean([r["total_time"] for r in size_results])
        avg_task_time = statistics.mean([r["avg_task_time"] for r in size_results])
        avg_tasks_per_second = statistics.mean([r["tasks_per_second"] for r in size_results])
        
        results[str(size)] = {
            "workload_size": size,
            "repetitions": repetitions,
            "num_tasks": num_tasks,
            "avg_total_time": avg_total_time,
            "avg_task_time": avg_task_time,
            "avg_tasks_per_second": avg_tasks_per_second,
            "detailed_results": size_results
        }
    
    return {
        "benchmark_type": "cpu",
        "results": results
    }

def run_memory_benchmark(
    repetitions: int = 3,
    memory_sizes_mb: Optional[List[int]] = None,
    num_tasks: int = 5
) -> Dict[str, Any]:
    """
    Run memory-intensive benchmark
    
    Args:
        repetitions: Number of times to repeat each workload
        memory_sizes_mb: List of memory sizes to test in MB
        num_tasks: Number of tasks to run for each memory size
        
    Returns:
        Dictionary with benchmark results
    """
    if memory_sizes_mb is None:
        memory_sizes_mb = [100, 500, 1000]
    
    logger.info("Running memory benchmark")
    
    results = {}
    
    for size_mb in memory_sizes_mb:
        logger.info(f"Testing memory size: {size_mb} MB")
        
        size_results = []
        for rep in range(repetitions):
            logger.info(f"Repetition {rep+1}/{repetitions}")
            
            # Create tasks
            tasks = [memory_intensive_task.remote(size_mb) for _ in range(num_tasks)]
            
            # Measure total time
            start_time = time.time()
            task_results = ray.get(tasks)
            total_time = time.time() - start_time
            
            # Calculate task times
            successful_tasks = [r for r in task_results if r.get("success", False)]
            failed_tasks = [r for r in task_results if not r.get("success", False)]
            
            if successful_tasks:
                task_times = [result["elapsed_time"] for result in successful_tasks]
                
                # Record results
                rep_result = {
                    "total_time": total_time,
                    "successful_tasks": len(successful_tasks),
                    "failed_tasks": len(failed_tasks),
                    "task_times": task_times,
                    "avg_task_time": statistics.mean(task_times) if task_times else 0,
                    "min_task_time": min(task_times) if task_times else 0,
                    "max_task_time": max(task_times) if task_times else 0,
                    "std_dev": statistics.stdev(task_times) if len(task_times) > 1 else 0,
                    "mb_per_second": (size_mb * len(successful_tasks)) / total_time if total_time > 0 else 0
                }
            else:
                rep_result = {
                    "total_time": total_time,
                    "successful_tasks": 0,
                    "failed_tasks": len(failed_tasks),
                    "error": "All tasks failed"
                }
            
            size_results.append(rep_result)
        
        # Aggregate results across repetitions
        successful_reps = [r for r in size_results if r.get("successful_tasks", 0) > 0]
        
        if successful_reps:
            avg_total_time = statistics.mean([r["total_time"] for r in successful_reps])
            avg_task_time = statistics.mean([r["avg_task_time"] for r in successful_reps])
            avg_mb_per_second = statistics.mean([r["mb_per_second"] for r in successful_reps])
            
            results[str(size_mb)] = {
                "memory_size_mb": size_mb,
                "repetitions": repetitions,
                "num_tasks": num_tasks,
                "avg_total_time": avg_total_time,
                "avg_task_time": avg_task_time,
                "avg_mb_per_second": avg_mb_per_second,
                "detailed_results": size_results
            }
        else:
            results[str(size_mb)] = {
                "memory_size_mb": size_mb,
                "repetitions": repetitions,
                "num_tasks": num_tasks,
                "error": "All repetitions failed",
                "detailed_results": size_results
            }
    
    return {
        "benchmark_type": "memory",
        "results": results
    }

def run_batch_benchmark(
    repetitions: int = 3,
    batch_sizes: Optional[List[int]] = None,
    num_batches: int = 10,
    work_factor: int = 100000
) -> Dict[str, Any]:
    """
    Run batch processing benchmark
    
    Args:
        repetitions: Number of times to repeat each workload
        batch_sizes: List of batch sizes to test
        num_batches: Number of batches to process for each size
        work_factor: Factor controlling work per item
        
    Returns:
        Dictionary with benchmark results
    """
    if batch_sizes is None:
        batch_sizes = [10, 50, 100, 500]
    
    logger.info("Running batch processing benchmark")
    
    results = {}
    
    for batch_size in batch_sizes:
        logger.info(f"Testing batch size: {batch_size}")
        
        size_results = []
        for rep in range(repetitions):
            logger.info(f"Repetition {rep+1}/{repetitions}")
            
            # Create batches
            batches = []
            for _ in range(num_batches):
                batch = [random.randint(1, 1000) for _ in range(batch_size)]
                batches.append(batch)
            
            # Create tasks
            tasks = [process_batch.remote(batch, work_factor) for batch in batches]
            
            # Measure total time
            start_time = time.time()
            task_results = ray.get(tasks)
            total_time = time.time() - start_time
            
            # Calculate task times
            task_times = [result["elapsed_time"] for result in task_results]
            
            # Record results
            rep_result = {
                "total_time": total_time,
                "task_times": task_times,
                "avg_task_time": statistics.mean(task_times),
                "min_task_time": min(task_times),
                "max_task_time": max(task_times),
                "std_dev": statistics.stdev(task_times) if len(task_times) > 1 else 0,
                "items_per_second": (batch_size * num_batches) / total_time,
                "batches_per_second": num_batches / total_time
            }
            size_results.append(rep_result)
        
        # Aggregate results across repetitions
        avg_total_time = statistics.mean([r["total_time"] for r in size_results])
        avg_task_time = statistics.mean([r["avg_task_time"] for r in size_results])
        avg_items_per_second = statistics.mean([r["items_per_second"] for r in size_results])
        
        results[str(batch_size)] = {
            "batch_size": batch_size,
            "num_batches": num_batches,
            "work_factor": work_factor,
            "repetitions": repetitions,
            "avg_total_time": avg_total_time,
            "avg_task_time": avg_task_time,
            "avg_items_per_second": avg_items_per_second,
            "detailed_results": size_results
        }
    
    return {
        "benchmark_type": "batch",
        "results": results
    }

def run_all_benchmarks(
    output_file: Optional[str] = None,
    cpu_benchmark: bool = True,
    memory_benchmark: bool = True,
    batch_benchmark: bool = True,
    repetitions: int = 3
) -> Dict[str, Any]:
    """
    Run all benchmarks
    
    Args:
        output_file: File to write benchmark results to
        cpu_benchmark: Whether to run CPU benchmark
        memory_benchmark: Whether to run memory benchmark
        batch_benchmark: Whether to run batch benchmark
        repetitions: Number of repetitions for each benchmark
        
    Returns:
        Dictionary with all benchmark results
    """
    start_time = time.time()
    
    # Initialize Ray if not already
    if not ray.is_initialized():
        try:
            ray.init(address="auto", ignore_reinit_error=True)
            logger.info("Connected to existing Ray cluster")
        except ConnectionError:
            ray.init(ignore_reinit_error=True)
            logger.info("Started new local Ray instance")
    
    # Get cluster resources
    resources = get_cluster_resources()
    logger.info(f"Running benchmarks on cluster with {resources['total_nodes']} nodes, "
                f"{resources['total_cpus']} CPUs, {resources['total_memory_gb']:.1f} GB memory")
    
    # Run benchmarks
    all_results = {
        "timestamp": time.time(),
        "cluster_resources": resources,
        "benchmarks": {}
    }
    
    if cpu_benchmark:
        logger.info("Starting CPU benchmark")
        cpu_results = run_cpu_benchmark(repetitions=repetitions)
        all_results["benchmarks"]["cpu"] = cpu_results
        logger.info("CPU benchmark completed")
    
    if memory_benchmark:
        logger.info("Starting memory benchmark")
        memory_results = run_memory_benchmark(repetitions=repetitions)
        all_results["benchmarks"]["memory"] = memory_results
        logger.info("Memory benchmark completed")
    
    if batch_benchmark:
        logger.info("Starting batch processing benchmark")
        batch_results = run_batch_benchmark(repetitions=repetitions)
        all_results["benchmarks"]["batch"] = batch_results
        logger.info("Batch processing benchmark completed")
    
    # Calculate total execution time
    all_results["total_execution_time"] = time.time() - start_time
    
    # Write results to file if specified
    if output_file:
        with open(output_file, 'w') as f:
            json.dump(all_results, f, indent=2)
        logger.info(f"Benchmark results written to {output_file}")
    
    return all_results

def print_summary(results: Dict[str, Any]) -> None:
    """
    Print a summary of benchmark results
    
    Args:
        results: Dictionary with benchmark results
    """
    print("\n" + "="*60)
    print("BENCHMARK SUMMARY")
    print("="*60)
    
    # Print cluster info
    if "cluster_resources" in results:
        r = results["cluster_resources"]
        print(f"Cluster:              {r.get('total_nodes', 0)} nodes, "
              f"{r.get('total_cpus', 0)} CPUs, "
              f"{r.get('total_gpus', 0)} GPUs, "
              f"{r.get('total_memory_gb', 0):.1f} GB memory")
    
    print(f"Total execution time: {results.get('total_execution_time', 0):.2f} seconds")
    print("-"*60)
    
    # Print CPU benchmark summary
    if "benchmarks" in results and "cpu" in results["benchmarks"]:
        cpu = results["benchmarks"]["cpu"]["results"]
        print("\nCPU Benchmark:")
        for size, result in cpu.items():
            print(f"  - Workload {size}: "
                  f"{result['avg_tasks_per_second']:.2f} tasks/sec, "
                  f"{result['avg_task_time']:.2f} sec/task")
    
    # Print memory benchmark summary
    if "benchmarks" in results and "memory" in results["benchmarks"]:
        memory = results["benchmarks"]["memory"]["results"]
        print("\nMemory Benchmark:")
        for size, result in memory.items():
            if "error" in result:
                print(f"  - {size} MB: Error - {result['error']}")
            else:
                print(f"  - {size} MB: "
                      f"{result['avg_mb_per_second']:.2f} MB/sec, "
                      f"{result['avg_task_time']:.2f} sec/task")
    
    # Print batch benchmark summary
    if "benchmarks" in results and "batch" in results["benchmarks"]:
        batch = results["benchmarks"]["batch"]["results"]
        print("\nBatch Processing Benchmark:")
        for size, result in batch.items():
            print(f"  - Batch size {size}: "
                  f"{result['avg_items_per_second']:.2f} items/sec, "
                  f"{result['avg_task_time']:.2f} sec/batch")
    
    print("="*60)

def main():
    parser = argparse.ArgumentParser(description="Run Ray cluster benchmarks")
    parser.add_argument("--output", "-o", type=str, help="Output file for benchmark results")
    parser.add_argument("--cpu", action="store_true", help="Run CPU benchmark only")
    parser.add_argument("--memory", action="store_true", help="Run memory benchmark only")
    parser.add_argument("--batch", action="store_true", help="Run batch processing benchmark only")
    parser.add_argument("--all", action="store_true", help="Run all benchmarks (default)")
    parser.add_argument("--repetitions", "-r", type=int, default=3, help="Number of repetitions for each benchmark (default: 3)")
    args = parser.parse_args()
    
    # If no specific benchmarks are selected, run all
    run_all = args.all or not (args.cpu or args.memory or args.batch)
    
    # Run benchmarks
    try:
        results = run_all_benchmarks(
            output_file=args.output,
            cpu_benchmark=args.cpu or run_all,
            memory_benchmark=args.memory or run_all,
            batch_benchmark=args.batch or run_all,
            repetitions=args.repetitions
        )
        
        # Print summary
        print_summary(results)
        
        # Shutdown Ray
        ray.shutdown()
        
    except Exception as e:
        logger.error(f"Error running benchmarks: {str(e)}")
        ray.shutdown()
        sys.exit(1)

if __name__ == "__main__":
    main()
