#!/usr/bin/env python3
"""
Script for running code formatters in parallel using Ray cluster.
Supports black and isort for Python code formatting.
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Any

import ray

# Add the project root to the Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, project_root)

from ray_tasks.resource_utils import get_cluster_resources

# Configure logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# List of supported formatters
SUPPORTED_FORMATTERS = ["black", "isort"]

def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Run code formatters in parallel using Ray")
    parser.add_argument(
        "--ray-address",
        type=str,
        default="auto",
        help="Ray cluster address (default: auto)",
    )
    parser.add_argument(
        "--directory",
        type=str,
        default=".",
        help="Directory to format (default: current directory)",
    )
    parser.add_argument(
        "--exclude",
        type=str,
        default="venv,env,.venv,.env,.git,__pycache__,*.pyc,*.pyo,*.pyd,build,dist",
        help="Comma-separated list of patterns to exclude",
    )
    parser.add_argument(
        "--formatters",
        type=str,
        default="black,isort",
        help="Comma-separated list of formatters to use (default: black,isort)",
    )
    parser.add_argument(
        "--black-line-length",
        type=int,
        default=88,
        help="Line length for Black formatter (default: 88)",
    )
    parser.add_argument(
        "--isort-profile",
        type=str,
        default="black",
        help="Profile for isort formatter (default: black)",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only check if files would be reformatted, don't modify them",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output file for formatting results (default: stdout)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    return parser.parse_args()

def find_python_files(directory: str, exclude_patterns: List[str]) -> List[str]:
    """Find all Python files in a directory, excluding specified patterns."""
    python_files = []
    exclude_dirs = set()
    
    # Process exclude patterns for directories
    for pattern in exclude_patterns:
        if not pattern.startswith("*."):
            exclude_dirs.add(pattern)
    
    for root, dirs, files in os.walk(directory):
        # Skip excluded directories
        dirs[:] = [d for d in dirs if d not in exclude_dirs and not any(
            os.path.join(root, d).startswith(os.path.join(directory, excl))
            for excl in exclude_dirs
        )]
        
        for file in files:
            if file.endswith(".py"):
                file_path = os.path.join(root, file)
                # Check if file matches any exclude pattern
                if not any(
                    pattern.startswith("*.") and file.endswith(pattern[1:])
                    for pattern in exclude_patterns
                ):
                    python_files.append(file_path)
    
    logger.info(f"Found {len(python_files)} Python files to format")
    return python_files

@ray.remote
def format_file(file_path: str, formatters: List[str], check_only: bool, 
                black_line_length: int, isort_profile: str) -> Dict[str, Any]:
    """Format a Python file using the specified formatters."""
    result = {
        "file": file_path,
        "formatters": {},
        "success": True,
        "changed": False,
    }
    
    for formatter in formatters:
        formatter_result = {
            "success": True,
            "output": "",
            "error": "",
            "changed": False,
        }
        
        try:
            if formatter == "black":
                cmd = ["black"]
                if check_only:
                    cmd.append("--check")
                cmd.extend(["--line-length", str(black_line_length), file_path])
                
            elif formatter == "isort":
                cmd = ["isort"]
                if check_only:
                    cmd.append("--check")
                cmd.extend(["--profile", isort_profile, file_path])
                
            elif formatter == "yapf":
                cmd = ["yapf"]
                if check_only:
                    cmd.append("--diff")
                else:
                    cmd.append("--in-place")
                cmd.append(file_path)
                
            else:
                formatter_result["success"] = False
                formatter_result["error"] = f"Unknown formatter: {formatter}"
                result["formatters"][formatter] = formatter_result
                result["success"] = False
                continue
            
            # Run the formatter
            process = subprocess.run(
                cmd, 
                capture_output=True, 
                text=True,
                check=False
            )
            
            if process.returncode == 0:
                formatter_result["output"] = process.stdout.strip()
                formatter_result["changed"] = False
            else:
                # If check_only is True, non-zero return code might just mean formatting is needed
                if check_only and formatter in ["black", "isort"]:
                    formatter_result["output"] = process.stdout.strip()
                    formatter_result["error"] = process.stderr.strip()
                    formatter_result["changed"] = True
                    formatter_result["success"] = True
                else:
                    formatter_result["output"] = process.stdout.strip()
                    formatter_result["error"] = process.stderr.strip()
                    formatter_result["success"] = False
            
            if formatter_result["changed"]:
                result["changed"] = True
                
        except Exception as e:
            formatter_result["success"] = False
            formatter_result["error"] = str(e)
            result["success"] = False
        
        result["formatters"][formatter] = formatter_result
    
    return result

def aggregate_format_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate formatting results from multiple files."""
    aggregate = {
        "total_files": len(results),
        "successfully_formatted": 0,
        "failed_formatting": 0,
        "files_changed": 0,
        "formatter_stats": {},
        "file_results": results,
    }
    
    for result in results:
        if result["success"]:
            aggregate["successfully_formatted"] += 1
        else:
            aggregate["failed_formatting"] += 1
        
        if result["changed"]:
            aggregate["files_changed"] += 1
        
        # Aggregate per-formatter statistics
        for formatter, formatter_result in result["formatters"].items():
            if formatter not in aggregate["formatter_stats"]:
                aggregate["formatter_stats"][formatter] = {
                    "success": 0,
                    "failure": 0,
                    "files_changed": 0,
                }
            
            if formatter_result["success"]:
                aggregate["formatter_stats"][formatter]["success"] += 1
            else:
                aggregate["formatter_stats"][formatter]["failure"] += 1
            
            if formatter_result["changed"]:
                aggregate["formatter_stats"][formatter]["files_changed"] += 1
    
    return aggregate

def write_results_to_file(results: Dict[str, Any], output_file: str) -> None:
    """Write formatting results to a JSON file."""
    try:
        with open(output_file, "w") as f:
            json.dump(results, f, indent=2)
        logger.info(f"Formatting results written to {output_file}")
    except Exception as e:
        logger.error(f"Failed to write results to {output_file}: {e}")

def print_summary(results: Dict[str, Any], check_only: bool) -> None:
    """Print a summary of the formatting results."""
    logger.info("\nFormatting Summary:")
    logger.info("------------------")
    logger.info(f"Total files: {results['total_files']}")
    
    if check_only:
        logger.info(f"Files that would be reformatted: {results['files_changed']}")
        logger.info(f"Files that would be left unchanged: {results['total_files'] - results['files_changed']}")
    else:
        logger.info(f"Files successfully formatted: {results['successfully_formatted']}")
        logger.info(f"Files with formatting errors: {results['failed_formatting']}")
        logger.info(f"Files changed: {results['files_changed']}")
    
    logger.info("\nFormatter Statistics:")
    for formatter, stats in results["formatter_stats"].items():
        if check_only:
            logger.info(f"  {formatter}: {stats['files_changed']} files would be reformatted")
        else:
            logger.info(f"  {formatter}: {stats['success']} successes, {stats['failure']} failures, {stats['files_changed']} files changed")
    
    # Print files with errors if any
    if results['failed_formatting'] > 0:
        logger.info("\nFiles with formatting errors:")
        for file_result in results['file_results']:
            if not file_result['success']:
                logger.info(f"  {file_result['file']}")
                for formatter, formatter_result in file_result['formatters'].items():
                    if not formatter_result['success']:
                        error_msg = formatter_result['error']
                        if error_msg:
                            logger.info(f"    {formatter}: {error_msg.split(os.linesep)[0]}")  # Only first line of error

def main() -> int:
    """Main entry point for the formatting script."""
    args = parse_args()
    
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    
    # Convert directory to absolute path
    directory = os.path.abspath(args.directory)
    
    if not os.path.isdir(directory):
        logger.error(f"Directory {directory} does not exist or is not a directory")
        return 1
    
    # Parse formatters
    formatters = args.formatters.split(",")
    logger.info(f"Using formatters: {', '.join(formatters)}")
    
    # Parse exclude patterns
    exclude_patterns = args.exclude.split(",")
    
    # Initialize Ray
    try:
        ray.init(address=args.ray_address)
        logger.info(f"Connected to Ray cluster at {args.ray_address}")
    except Exception as e:
        logger.error(f"Failed to connect to Ray cluster: {e}")
        return 1
    
    try:
        # Find Python files
        python_files = find_python_files(directory, exclude_patterns)
        logger.info(f"Found {len(python_files)} Python files to format")
        
        if not python_files:
            logger.info("No Python files found. Exiting.")
            return 0
        
        # Format files in parallel
        format_tasks = [
            format_file.remote(
                file_path, 
                formatters, 
                args.check_only,
                args.black_line_length,
                args.isort_profile
            )
            for file_path in python_files
        ]
        
        # Get results
        format_results = ray.get(format_tasks)
        
        # Aggregate results
        aggregated_results = aggregate_format_results(format_results)
        
        # Print summary
        print_summary(aggregated_results, args.check_only)
        
        # Write results to file if specified
        if args.output:
            write_results_to_file(aggregated_results, args.output)
        
        # Return appropriate exit code
        if args.check_only and aggregated_results["files_changed"] > 0:
            logger.info("Check failed: some files would be reformatted")
            return 1
        elif not args.check_only and aggregated_results["failed_formatting"] > 0:
            logger.info("Formatting failed for some files")
            return 1
        
        return 0
        
    except Exception as e:
        logger.error(f"Error: {e}")
        return 1
    finally:
        # Shutdown Ray
        ray.shutdown()
        logger.info("Ray shutdown complete")

if __name__ == "__main__":
    exit(main())
