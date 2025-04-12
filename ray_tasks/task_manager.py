#!/usr/bin/env python3
"""
Task manager for Ray cluster - handles distributed task execution,
error handling, and progress tracking.
"""

import ray
import os
import time
import logging
from typing import List, Callable, Any, Dict, Optional, Union, Tuple
from functools import wraps
import traceback

from .resource_utils import get_optimal_resource_allocation

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def retry_task(max_attempts: int = 3, delay: int = 2):
    """
    Decorator to retry Ray tasks on failure
    
    Args:
        max_attempts: Maximum number of retry attempts
        delay: Delay between retries in seconds
        
    Returns:
        Decorated function with retry logic
    """
    def decorator(task_func):
        @wraps(task_func)
        def wrapper(*args, **kwargs):
            attempts = 0
            last_exception = None
            
            while attempts < max_attempts:
                try:
                    return task_func(*args, **kwargs)
                except Exception as e:
                    attempts += 1
                    last_exception = e
                    
                    if attempts == max_attempts:
                        logger.error(
                            f"Task {task_func.__name__} failed after {max_attempts} attempts. "
                            f"Error: {str(e)}\n{traceback.format_exc()}"
                        )
                        raise
                    
                    logger.warning(
                        f"Task {task_func.__name__} failed (attempt {attempts}/{max_attempts}), "
                        f"retrying in {delay}s. Error: {str(e)}"
                    )
                    time.sleep(delay)
            
            # This should never be reached due to the raise in the loop,
            # but just in case:
            raise last_exception
            
        return wrapper
    return decorator

def distribute_tasks(
    task_func: Callable,
    items: List[Any],
    task_type: str = "default",
    batch_size: int = 1,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    timeout: Optional[int] = None,
    retry_attempts: int = 3,
    retry_delay: int = 2,
    memory_intensive: bool = False,
    file_sizes: Optional[List[int]] = None
) -> List[Any]:
    """
    Distribute tasks across the Ray cluster with optimal resource allocation
    
    Args:
        task_func: The Ray remote function to execute
        items: List of items to process
        task_type: Type of task for resource allocation
        batch_size: Number of items to process in each task
        progress_callback: Optional callback for progress updates
        timeout: Optional timeout in seconds for each task
        retry_attempts: Number of retry attempts for failed tasks
        retry_delay: Delay between retries in seconds
        memory_intensive: Whether the task requires significant memory
        file_sizes: List of file sizes in bytes (corresponding to items)
        
    Returns:
        List of results from all tasks
    """
    if not ray.is_initialized():
        try:
            ray.init(address="auto", ignore_reinit_error=True)
            logger.info("Connected to existing Ray cluster")
        except ConnectionError:
            ray.init(ignore_reinit_error=True)
            logger.info("Started new local Ray instance")
    
    total_items = len(items)
    logger.info(f"Distributing {total_items} items across Ray cluster (batch_size={batch_size})")
    
    # If file sizes are provided, they should match the number of items
    if file_sizes and len(file_sizes) != len(items):
        logger.warning(
            f"file_sizes length ({len(file_sizes)}) doesn't match items length ({len(items)}). "
            "Ignoring file_sizes."
        )
        file_sizes = None
    
    # Process in batches if specified
    if batch_size > 1:
        # Create batches of items
        batched_items = [items[i:i+batch_size] for i in range(0, len(items), batch_size)]
        
        # Create batches of file sizes if available
        batched_file_sizes = None
        if file_sizes:
            batched_file_sizes = [file_sizes[i:i+batch_size] for i in range(0, len(file_sizes), batch_size)]
        
        logger.info(f"Created {len(batched_items)} batches of size {batch_size}")
        
        # Create remote tasks for each batch
        futures = []
        for i, batch in enumerate(batched_items):
            # Get optimal resource allocation
            if batched_file_sizes:
                # Use the maximum file size in the batch for resource allocation
                max_file_size = max(batched_file_sizes[i])
                resources = get_optimal_resource_allocation(
                    task_type=task_type,
                    file_size=max_file_size,
                    memory_intensive=memory_intensive
                )
            else:
                resources = get_optimal_resource_allocation(
                    task_type=task_type,
                    memory_intensive=memory_intensive
                )
            
            # Create optimized remote function with the computed resources
            optimized_task = ray.remote(**resources)(task_func)
            
            # Submit the task
            futures.append(optimized_task.remote(batch))
            
    else:
        # Process items individually
        futures = []
        for i, item in enumerate(items):
            # Get optimal resource allocation
            if file_sizes:
                resources = get_optimal_resource_allocation(
                    task_type=task_type,
                    file_size=file_sizes[i],
                    memory_intensive=memory_intensive
                )
            else:
                resources = get_optimal_resource_allocation(
                    task_type=task_type,
                    memory_intensive=memory_intensive
                )
            
            # Create optimized remote function with the computed resources
            optimized_task = ray.remote(**resources)(task_func)
            
            # Submit the task
            futures.append(optimized_task.remote(item))
    
    # Get results with optional progress reporting and timeout
    results = []
    pending_futures = list(futures)
    
    start_time = time.time()
    completed = 0
    
    while pending_futures:
        # Use ray.wait to get completed futures
        done_futures, pending_futures = ray.wait(
            pending_futures,
            num_returns=1,  # Wait for at least one task to complete
            timeout=timeout  # Use provided timeout or None for no timeout
        )
        
        # Get results from completed futures
        for future in done_futures:
            try:
                result = ray.get(future)
                results.append(result)
                completed += 1
                
                # Report progress if callback is provided
                if progress_callback:
                    progress_callback(completed, len(futures))
                
            except Exception as e:
                logger.error(f"Task failed: {str(e)}")
                # Retry logic
                if retry_attempts > 0:
                    logger.info(f"Retrying failed task ({retry_attempts} attempts left)")
                    
                    # Get the original task and resubmit
                    # Note: This is simplified and might not work for all cases
                    # since we don't know the original arguments
                    retry_future = ray.remote(**resources)(task_func).remote(items[completed])
                    pending_futures.append(retry_future)
                    
                    retry_attempts -= 1
                    time.sleep(retry_delay)
                else:
                    # Add None or error indicator to results
                    results.append({"error": str(e)})
                    
        # Check for timeout on entire operation if specified
        if timeout and (time.time() - start_time > timeout):
            logger.warning(f"Operation timed out after {timeout} seconds")
            # Add None for remaining tasks
            results.extend([None] * len(pending_futures))
            break
    
    logger.info(f"Completed {completed}/{len(futures)} tasks")
    
    # Handle batched results if batch_size > 1
    if batch_size > 1:
        # Flatten results if they're in batches
        flattened_results = []
        for batch_result in results:
            if isinstance(batch_result, list):
                flattened_results.extend(batch_result)
            else:
                # Handle the case where a batch returns a single result
                flattened_results.append(batch_result)
        return flattened_results
    else:
        return results

def execute_in_parallel(
    functions: List[Tuple[Callable, List, Dict]],
    task_type: str = "default"
) -> List[Any]:
    """
    Execute multiple different functions in parallel
    
    Args:
        functions: List of tuples (function, args, kwargs)
        task_type: Type of task for resource allocation
        
    Returns:
        List of results from all functions
    """
    if not ray.is_initialized():
        ray.init(address="auto", ignore_reinit_error=True)
    
    futures = []
    
    for func, args, kwargs in functions:
        # Get optimal resource allocation
        resources = get_optimal_resource_allocation(task_type=task_type)
        
        # Create remote task with resources
        remote_func = ray.remote(**resources)(func)
        
        # Submit task with args and kwargs
        futures.append(remote_func.remote(*args, **kwargs))
    
    # Get all results
    return ray.get(futures)

if __name__ == "__main__":
    # Example usage
    import random
    
    @ray.remote
    def process_item(item):
        # Simulate processing time
        time.sleep(random.uniform(0.1, 0.5))
        return f"Processed: {item}"
    
    # Define a progress callback
    def show_progress(current, total):
        print(f"Progress: {current}/{total} ({current/total*100:.1f}%)")
    
    # Create sample data
    sample_data = [f"item_{i}" for i in range(20)]
    
    # Distribute tasks
    results = distribute_tasks(
        process_item,
        sample_data,
        batch_size=4,
        progress_callback=show_progress
    )
    
    print("Results:", results)