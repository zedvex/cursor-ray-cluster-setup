#!/usr/bin/env python3
"""
Example of distributed file processing using the Ray cluster.
This example shows how to efficiently process large files or many small files
by distributing the work across a Ray cluster.
"""

import os
import sys
import time
import argparse
import logging
from typing import List, Dict, Any, Tuple
from pathlib import Path
import ray

# Add the project root to the Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, project_root)

from ray_tasks.task_manager import distribute_tasks
from ray_tasks.resource_utils import get_optimal_resource_allocation
from ray_tasks.error_handling import retry

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Example file processing task
@ray.remote
def process_file(file_path: str) -> Dict[str, Any]:
    """
    Process a single file and return statistics
    
    Args:
        file_path: Path to the file to process
        
    Returns:
        Dictionary with processing results and statistics
    """
    start_time = time.time()
    file_size = os.path.getsize(file_path)
    
    try:
        # Simulate some CPU-intensive work
        # In a real application, this would be actual file processing
        with open(file_path, 'rb') as f:
            # Read the file in chunks to avoid loading large files entirely in memory
            chunk_size = 1024 * 1024  # 1MB chunks
            line_count = 0
            word_count = 0
            char_count = 0
            
            # Process the file in chunks
            for chunk in iter(lambda: f.read(chunk_size), b''):
                # Convert to string, handling potential decoding errors
                try:
                    text = chunk.decode('utf-8')
                except UnicodeDecodeError:
                    # Fallback to latin-1 if utf-8 fails
                    text = chunk.decode('latin-1')
                
                # Count lines, words, and characters
                line_count += text.count('\n')
                word_count += len(text.split())
                char_count += len(text)
                
                # Simulate some computation time
                time.sleep(0.001 * len(text) / 10000)  # Longer processing for larger chunks
        
        elapsed_time = time.time() - start_time
        
        return {
            "file_path": file_path,
            "file_size": file_size,
            "line_count": line_count,
            "word_count": word_count,
            "char_count": char_count,
            "processing_time": elapsed_time,
            "status": "success"
        }
        
    except Exception as e:
        elapsed_time = time.time() - start_time
        logger.error(f"Error processing file {file_path}: {str(e)}")
        
        return {
            "file_path": file_path,
            "file_size": file_size,
            "error": str(e),
            "processing_time": elapsed_time,
            "status": "error"
        }

@ray.remote
def batch_process_files(file_paths: List[str]) -> List[Dict[str, Any]]:
    """
    Process a batch of files and return statistics for each
    
    Args:
        file_paths: List of paths to files to process
        
    Returns:
        List of dictionaries with processing results
    """
    results = []
    for file_path in file_paths:
        try:
            result = process_file.remote(file_path)
            results.append(ray.get(result))
        except Exception as e:
            logger.error(f"Error in batch processing for file {file_path}: {str(e)}")
            results.append({
                "file_path": file_path,
                "error": str(e),
                "status": "error"
            })
    
    return results

def find_files(directory: str, pattern: str = "*", recursive: bool = False) -> List[Tuple[str, int]]:
    """
    Find files matching the pattern in the directory
    
    Args:
        directory: Directory to search
        pattern: File pattern to match (glob pattern)
        recursive: Whether to search recursively
        
    Returns:
        List of tuples (file_path, file_size)
    """
    path = Path(directory)
    
    if recursive:
        glob_pattern = f"**/{pattern}"
    else:
        glob_pattern = pattern
    
    # Find all matching files and get their sizes
    files_with_sizes = []
    for file_path in path.glob(glob_pattern):
        if file_path.is_file():
            file_size = file_path.stat().st_size
            files_with_sizes.append((str(file_path), file_size))
    
    return files_with_sizes

def process_directory(
    directory: str,
    pattern: str = "*",
    recursive: bool = False,
    batch_size: int = 10,
    small_file_threshold: int = 1024 * 100,  # 100KB
    progress_interval: int = 5
) -> Dict[str, Any]:
    """
    Process all files in a directory that match the specified pattern
    
    Args:
        directory: Directory containing files to process
        pattern: File pattern to match (glob pattern)
        recursive: Whether to search recursively
        batch_size: Number of small files to process in each batch
        small_file_threshold: Threshold for small files in bytes
        progress_interval: Interval in seconds for progress updates
        
    Returns:
        Dictionary with overall processing statistics
    """
    # Initialize Ray if not already
    if not ray.is_initialized():
        try:
            ray.init(address="auto", ignore_reinit_error=True)
            logger.info("Connected to existing Ray cluster")
        except ConnectionError:
            ray.init(ignore_reinit_error=True)
            logger.info("Started new local Ray instance")
    
    # Find all files and get their sizes
    logger.info(f"Searching for files matching '{pattern}' in {directory}...")
    files_with_sizes = find_files(directory, pattern, recursive)
    
    if not files_with_sizes:
        logger.warning(f"No files found matching '{pattern}' in {directory}")
        return {
            "status": "error",
            "error": f"No files found matching '{pattern}' in {directory}"
        }
    
    logger.info(f"Found {len(files_with_sizes)} files to process")
    
    # Separate large and small files
    large_files = []
    small_files = []
    file_sizes = []
    
    for file_path, file_size in files_with_sizes:
        if file_size > small_file_threshold:
            large_files.append(file_path)
        else:
            small_files.append(file_path)
        file_sizes.append(file_size)
    
    logger.info(f"Processing {len(large_files)} large files individually and "
                f"{len(small_files)} small files in batches of {batch_size}")
    
    # Process large files individually
    large_file_results = []
    if large_files:
        # Define a progress callback
        def report_progress(completed, total):
            logger.info(f"Progress: {completed}/{total} large files processed "
                        f"({completed/total*100:.1f}%)")
        
        large_file_results = distribute_tasks(
            task_func=process_file,
            items=large_files,
            task_type="cpu_intensive",
            progress_callback=report_progress,
            retry_attempts=3,
            file_sizes=[size for _, size in files_with_sizes if size > small_file_threshold]
        )
    
    # Process small files in batches
    small_file_batches = []
    if small_files:
        # Create batches of small files
        for i in range(0, len(small_files), batch_size):
            small_file_batches.append(small_files[i:i+batch_size])
        
        # Process each batch
        batch_results = distribute_tasks(
            task_func=batch_process_files,
            items=small_file_batches,
            task_type="cpu_intensive",
            retry_attempts=2
        )
        
        # Flatten the results
        small_file_results = [result for batch in batch_results for result in batch]
    else:
        small_file_results = []
    
    # Combine all results
    all_results = large_file_results + small_file_results
    
    # Calculate overall statistics
    total_size = sum(result.get("file_size", 0) for result in all_results)
    total_lines = sum(result.get("line_count", 0) for result in all_results)
    total_words = sum(result.get("word_count", 0) for result in all_results)
    total_chars = sum(result.get("char_count", 0) for result in all_results)
    
    successful = sum(1 for result in all_results if result.get("status") == "success")
    failed = sum(1 for result in all_results if result.get("status") == "error")
    
    total_time = sum(result.get("processing_time", 0) for result in all_results)
    
    # Return summary
    return {
        "status": "success",
        "files_processed": len(all_results),
        "successful": successful,
        "failed": failed,
        "total_size_bytes": total_size,
        "total_size_mb": total_size / (1024 * 1024),
        "total_lines": total_lines,
        "total_words": total_words,
        "total_chars": total_chars,
        "total_processing_time": total_time,
        "parallelism_speedup": total_time / max(time.time() - start_time, 0.001) if 'start_time' in locals() else 1,
        "results": all_results
    }

# Command-line interface
def main():
    parser = argparse.ArgumentParser(description="Distributed file processing example")
    parser.add_argument("directory", help="Directory containing files to process")
    parser.add_argument("--pattern", default="*", help="File pattern to match (glob pattern)")
    parser.add_argument("--recursive", action="store_true", help="Search recursively")
    parser.add_argument("--batch-size", type=int, default=10, help="Number of small files to process in each batch")
    parser.add_argument("--small-file-threshold", type=int, default=102400, help="Threshold for small files in bytes (default: 100KB)")
    args = parser.parse_args()
    
    start_time = time.time()
    logger.info(f"Starting distributed file processing for {args.directory}")
    
    # Process the directory
    results = process_directory(
        directory=args.directory,
        pattern=args.pattern,
        recursive=args.recursive,
        batch_size=args.batch_size,
        small_file_threshold=args.small_file_threshold
    )
    
    elapsed_time = time.time() - start_time
    
    # Print summary
    if results["status"] == "success":
        logger.info("=" * 50)
        logger.info("Processing Summary:")
        logger.info(f"Files processed: {results['files_processed']}")
        logger.info(f"Successful: {results['successful']}")
        logger.info(f"Failed: {results['failed']}")
        logger.info(f"Total size: {results['total_size_mb']:.2f} MB")
        logger.info(f"Total lines: {results['total_lines']}")
        logger.info(f"Total words: {results['total_words']}")
        logger.info(f"Total chars: {results['total_chars']}")
        logger.info(f"Time taken: {elapsed_time:.2f} seconds")
        logger.info(f"Estimated speedup: {results['parallelism_speedup']:.2f}x")
        logger.info("=" * 50)
    else:
        logger.error(f"Processing failed: {results.get('error', 'Unknown error')}")
    
    # Shutdown Ray
    ray.shutdown()

if __name__ == "__main__":
    main()