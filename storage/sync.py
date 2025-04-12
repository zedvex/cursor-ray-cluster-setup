#!/usr/bin/env python3
"""
File synchronization utilities for Ray cluster.
Provides functionality to synchronize files between nodes
when NFS is not available or not suitable.
"""

import os
import sys
import time
import argparse
import logging
import hashlib
import shutil
from typing import List, Dict, Any, Set
from pathlib import Path
import subprocess
import threading
import queue
import ray

# Add project root to Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, project_root)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def calculate_file_hash(filepath: str) -> str:
    """
    Calculate MD5 hash of a file
    
    Args:
        filepath: Path to the file
        
    Returns:
        MD5 hash string
    """
    hash_md5 = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()

def get_file_list(directory: str, exclude_patterns: List[str] = None) -> Dict[str, Dict[str, Any]]:
    """
    Get list of files in a directory with their metadata
    
    Args:
        directory: Directory to scan
        exclude_patterns: List of patterns to exclude
        
    Returns:
        Dictionary mapping relative file paths to their metadata
    """
    if exclude_patterns is None:
        exclude_patterns = [
            "__pycache__", 
            "*.pyc", 
            ".git", 
            "node_modules", 
            "venv", 
            ".env",
            "*.swp",
            "*.tmp"
        ]
    
    files = {}
    root_path = Path(directory)
    
    for path in root_path.glob("**/*"):
        # Skip directories
        if path.is_dir():
            continue
        
        # Check if file matches exclude patterns
        rel_path = str(path.relative_to(root_path))
        skip = False
        for pattern in exclude_patterns:
            if pattern.startswith("*"):
                if rel_path.endswith(pattern[1:]):
                    skip = True
                    break
            elif pattern in rel_path.split(os.path.sep):
                skip = True
                break
        
        if skip:
            continue
        
        # Get file stats
        stats = path.stat()
        files[rel_path] = {
            "size": stats.st_size,
            "mtime": stats.st_mtime,
            "path": str(path)
        }
    
    return files

@ray.remote
def calculate_file_hashes(file_list: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """
    Calculate hashes for a list of files
    
    Args:
        file_list: Dictionary of files with metadata
        
    Returns:
        Updated file list with hashes
    """
    result = {}
    for rel_path, metadata in file_list.items():
        try:
            hash_value = calculate_file_hash(metadata["path"])
            metadata["hash"] = hash_value
            result[rel_path] = metadata
        except Exception as e:
            logger.error(f"Error calculating hash for {rel_path}: {str(e)}")
    
    return result

def rsync_directory(
    source: str, 
    destination: str, 
    ssh_key: str = None, 
    exclude: List[str] = None,
    delete: bool = False,
    dry_run: bool = False
) -> bool:
    """
    Synchronize directories using rsync
    
    Args:
        source: Source directory
        destination: Destination directory or remote path (user@host:/path)
        ssh_key: Path to SSH key file for remote sync
        exclude: List of patterns to exclude
        delete: Whether to delete files in destination that don't exist in source
        dry_run: Whether to perform a dry run (no actual changes)
        
    Returns:
        True if sync was successful, False otherwise
    """
    if exclude is None:
        exclude = [
            "__pycache__/",
            "*.pyc",
            ".git/",
            "node_modules/",
            "venv/",
            ".env",
            "*.swp",
            "*.tmp"
        ]
    
    # Ensure source path ends with a slash to copy contents
    if not source.endswith("/"):
        source += "/"
    
    # Build rsync command
    cmd = ["rsync", "-avz"]
    
    # Add options
    if dry_run:
        cmd.append("--dry-run")
    
    if delete:
        cmd.append("--delete")
    
    # Add excludes
    for pattern in exclude:
        cmd.extend(["--exclude", pattern])
    
    # Add SSH key if provided
    if ssh_key and ":" in destination:
        cmd.extend(["-e", f"ssh -i {ssh_key} -o StrictHostKeyChecking=no"])
    
    # Add source and destination
    cmd.extend([source, destination])
    
    logger.info(f"Running rsync: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(
            cmd, 
            check=True, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            text=True
        )
        logger.debug(result.stdout)
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Rsync failed: {e.stderr}")
        return False

def sync_to_all_workers(
    source_dir: str,
    dest_dir: str,
    worker_nodes: List[str],
    ssh_key: str = None,
    exclude: List[str] = None,
    parallel: bool = True
) -> Dict[str, bool]:
    """
    Synchronize directory to all worker nodes
    
    Args:
        source_dir: Source directory
        dest_dir: Destination directory on workers
        worker_nodes: List of worker nodes (hostname or user@hostname)
        ssh_key: Path to SSH key file
        exclude: List of patterns to exclude
        parallel: Whether to sync in parallel
        
    Returns:
        Dictionary mapping nodes to success status
    """
    results = {}
    
    if parallel:
        # Parallel sync using threads
        result_queue = queue.Queue()
        threads = []
        
        def sync_worker(node):
            dest = f"{node}:{dest_dir}"
            success = rsync_directory(source_dir, dest, ssh_key, exclude)
            result_queue.put((node, success))
        
        # Start a thread for each worker
        for node in worker_nodes:
            thread = threading.Thread(target=sync_worker, args=(node,))
            thread.start()
            threads.append(thread)
        
        # Wait for all threads to complete
        for thread in threads:
            thread.join()
        
        # Collect results
        while not result_queue.empty():
            node, success = result_queue.get()
            results[node] = success
    
    else:
        # Sequential sync
        for node in worker_nodes:
            dest = f"{node}:{dest_dir}"
            success = rsync_directory(source_dir, dest, ssh_key, exclude)
            results[node] = success
    
    return results

def compare_directories(
    dir1: str, 
    dir2: str, 
    exclude_patterns: List[str] = None
) -> Dict[str, List[str]]:
    """
    Compare two directories and find differences
    
    Args:
        dir1: First directory
        dir2: Second directory
        exclude_patterns: List of patterns to exclude
        
    Returns:
        Dictionary with lists of added, removed, modified, and unchanged files
    """
    # Get file lists
    files1 = get_file_list(dir1, exclude_patterns)
    files2 = get_file_list(dir2, exclude_patterns)
    
    # Initialize Ray if needed
    if not ray.is_initialized():
        ray.init(ignore_reinit_error=True)
    
    # Calculate hashes in parallel
    files1_ref = ray.put(files1)
    files2_ref = ray.put(files2)
    
    files1_with_hashes = ray.get(calculate_file_hashes.remote(files1_ref))
    files2_with_hashes = ray.get(calculate_file_hashes.remote(files2_ref))
    
    # Compare directories
    all_files = set(files1_with_hashes.keys()) | set(files2_with_hashes.keys())
    only_in_dir1 = set(files1_with_hashes.keys()) - set(files2_with_hashes.keys())
    only_in_dir2 = set(files2_with_hashes.keys()) - set(files1_with_hashes.keys())
    
    modified = []
    unchanged = []
    
    for filename in all_files - only_in_dir1 - only_in_dir2:
        # File exists in both directories, compare hashes
        if files1_with_hashes[filename]["hash"] != files2_with_hashes[filename]["hash"]:
            modified.append(filename)
        else:
            unchanged.append(filename)
    
    return {
        "added": list(only_in_dir2),
        "removed": list(only_in_dir1),
        "modified": modified,
        "unchanged": unchanged
    }

def sync_based_on_diff(
    source_dir: str,
    dest_dir: str,
    diff: Dict[str, List[str]],
    use_rsync: bool = True
) -> bool:
    """
    Synchronize specific files based on diff analysis
    
    Args:
        source_dir: Source directory
        dest_dir: Destination directory
        diff: Difference dictionary from compare_directories
        use_rsync: Whether to use rsync for efficiency
        
    Returns:
        True if sync was successful
    """
    if use_rsync and (diff["added"] or diff["modified"]):
        # For efficiency, just use rsync for everything
        return rsync_directory(source_dir, dest_dir)
    
    # Manual sync
    source_base = Path(source_dir)
    dest_base = Path(dest_dir)
    
    # Create directories if needed
    os.makedirs(dest_dir, exist_ok=True)
    
    # Copy added and modified files
    for filename in diff["added"] + diff["modified"]:
        src_file = source_base / filename
        dst_file = dest_base / filename
        
        # Create parent directories if needed
        os.makedirs(os.path.dirname(dst_file), exist_ok=True)
        
        try:
            shutil.copy2(src_file, dst_file)
            logger.debug(f"Copied: {filename}")
        except Exception as e:
            logger.error(f"Error copying {filename}: {str(e)}")
            return False
    
    # Remove deleted files if needed
    for filename in diff["removed"]:
        dst_file = dest_base / filename
        try:
            if os.path.exists(dst_file):
                os.remove(dst_file)
                logger.debug(f"Removed: {filename}")
        except Exception as e:
            logger.error(f"Error removing {filename}: {str(e)}")
            return False
    
    return True

def main():
    parser = argparse.ArgumentParser(description="File synchronization utility for Ray cluster")
    
    # Create subparsers for different commands
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    # rsync command
    rsync_parser = subparsers.add_parser("rsync", help="Sync using rsync")
    rsync_parser.add_argument("source", help="Source directory")
    rsync_parser.add_argument("destination", help="Destination directory or remote path (user@host:/path)")
    rsync_parser.add_argument("--key", help="SSH key file for remote sync")
    rsync_parser.add_argument("--delete", action="store_true", help="Delete files in destination that don't exist in source")
    rsync_parser.add_argument("--dry-run", action="store_true", help="Perform a dry run (no actual changes)")
    rsync_parser.add_argument("--exclude", nargs="+", help="Patterns to exclude")
    
    # sync-to-workers command
    workers_parser = subparsers.add_parser("sync-to-workers", help="Sync to all worker nodes")
    workers_parser.add_argument("source", help="Source directory")
    workers_parser.add_argument("destination", help="Destination directory on workers")
    workers_parser.add_argument("--workers", nargs="+", required=True, help="List of worker nodes (hostname or user@hostname)")
    workers_parser.add_argument("--key", help="SSH key file")
    workers_parser.add_argument("--exclude", nargs="+", help="Patterns to exclude")
    workers_parser.add_argument("--sequential", action="store_true", help="Sync sequentially instead of in parallel")
    
    # compare command
    compare_parser = subparsers.add_parser("compare", help="Compare two directories")
    compare_parser.add_argument("dir1", help="First directory")
    compare_parser.add_argument("dir2", help="Second directory")
    compare_parser.add_argument("--exclude", nargs="+", help="Patterns to exclude")
    
    # sync-diff command
    diff_parser = subparsers.add_parser("sync-diff", help="Sync based on directory comparison")
    diff_parser.add_argument("source", help="Source directory")
    diff_parser.add_argument("destination", help="Destination directory")
    diff_parser.add_argument("--exclude", nargs="+", help="Patterns to exclude")
    diff_parser.add_argument("--manual", action="store_true", help="Use manual copy instead of rsync")
    
    args = parser.parse_args()
    
    if args.command == "rsync":
        success = rsync_directory(
            args.source, 
            args.destination, 
            args.key, 
            args.exclude, 
            args.delete, 
            args.dry_run
        )
        sys.exit(0 if success else 1)
    
    elif args.command == "sync-to-workers":
        results = sync_to_all_workers(
            args.source,
            args.destination,
            args.workers,
            args.key,
            args.exclude,
            not args.sequential
        )
        
        # Print results
        print("\nSync Results:")
        for node, success in results.items():
            status = "SUCCESS" if success else "FAILED"
            print(f"{node}: {status}")
        
        # Exit with error if any sync failed
        if not all(results.values()):
            sys.exit(1)
    
    elif args.command == "compare":
        diff = compare_directories(args.dir1, args.dir2, args.exclude)
        
        # Print comparison results
        print("\nDirectory Comparison Results:")
        print(f"Added files: {len(diff['added'])}")
        print(f"Removed files: {len(diff['removed'])}")
        print(f"Modified files: {len(diff['modified'])}")
        print(f"Unchanged files: {len(diff['unchanged'])}")
        
        # Print details if there are differences
        if diff["added"] or diff["removed"] or diff["modified"]:
            print("\nDetails:")
            
            if diff["added"]:
                print("\nAdded files:")
                for f in sorted(diff["added"]):
                    print(f"  + {f}")
            
            if diff["removed"]:
                print("\nRemoved files:")
                for f in sorted(diff["removed"]):
                    print(f"  - {f}")
            
            if diff["modified"]:
                print("\nModified files:")
                for f in sorted(diff["modified"]):
                    print(f"  ~ {f}")
    
    elif args.command == "sync-diff":
        # Compare directories
        diff = compare_directories(args.source, args.destination, args.exclude)
        
        # Print what will be updated
        total_changes = len(diff["added"]) + len(diff["removed"]) + len(diff["modified"])
        print(f"\nFound {total_changes} differences:")
        print(f"  {len(diff['added'])} files to add")
        print(f"  {len(diff['modified'])} files to update")
        print(f"  {len(diff['removed'])} files to remove")
        
        if total_changes > 0:
            # Confirm before proceeding
            confirm = input("\nProceed with sync? (y/n): ")
            if confirm.lower() != 'y':
                print("Sync cancelled.")
                sys.exit(0)
            
            # Perform sync
            success = sync_based_on_diff(
                args.source,
                args.destination,
                diff,
                not args.manual
            )
            
            if success:
                print("Sync completed successfully.")
            else:
                print("Sync failed.")
                sys.exit(1)
        else:
            print("Directories are already in sync.")
    
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
