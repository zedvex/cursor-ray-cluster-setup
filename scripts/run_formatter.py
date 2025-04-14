#!/usr/bin/env python3
"""
Script for running code formatters in parallel using Ray cluster.
Supports running black, isort, and other formatters on Python code.
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from glob import glob
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple, Union

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
        help="Directory to search for Python files (default: current directory)",
    )
    parser.add_argument(
        "--include",
        type=str,
        default="*.py",
        help="Glob pattern for files to include (default: *.py)",
    )
    parser.add_argument(
        "--exclude",
        type=str,
        default="",
        help="Comma-separated list of patterns to exclude",
    )
    parser.add_argument(
        "--formatters",
        type=str,
        default="black,isort",
        help="Comma-separated list of formatters to run (default: black,isort)",
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
        help="Profile for isort (default: black)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Run formatters in check mode (don't modify files)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose output",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output file for formatting results (default: stdout)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    return parser.parse_args()

def find_python_files(directory: str, include_pattern: str, exclude_patterns: List[str]) -> List[str]:
    """Find all Python files in a directory, excluding specified patterns."""
    all_files = glob(os.path.join(directory, "**", include_pattern), recursive=True)
    
    # Filter out excluded files
    if exclude_patterns:
        filtered_files = []
        for file_path in all_files:
            if not any(
                exclude_pattern in file_path
                for exclude_pattern in exclude_patterns
            ):
                filtered_files.append(file_path)
        files = filtered_files
    else:
        files = all_files
    
    # Sort files for consistent ordering
    files.sort()
    
    logger.info(f"Found {len(files)} Python files to format")
    return files

@ray.remote
def format_file(
    file_path: str,
    formatters: List[str],
    check_mode: bool = False,
    black_line_length: int = 88,
    isort_profile: str = "black",
    verbose: bool = False,
) -> Dict[str, Any]:
    """Format a single file using specified formatters."""
    start_time = time.time()
    result = {
        "file": file_path,
        "formatters": {},
        "success": True,
        "duration": 0,
        "errors": [],
    }
    
    for formatter in formatters:
        formatter_result = {
            "success": False,
            "output": "",
            "error": "",
            "modified": False,
        }
        
        try:
            if formatter == "black":
                cmd = ["black"]
                if check_mode:
                    cmd.append("--check")
                cmd.extend(["--line-length", str(black_line_length)])
                if verbose:
                    cmd.append("--verbose")
                cmd.append(file_path)
                
            elif formatter == "isort":
                cmd = ["isort"]
                if check_mode:
                    cmd.append("--check")
                cmd.extend(["--profile", isort_profile])
                if verbose:
                    cmd.append("--verbose")
                cmd.append(file_path)
                
            elif formatter == "autopep8":
                cmd = ["autopep8"]
                if not check_mode:
                    cmd.append("--in-place")
                cmd.extend(["--max-line-length", str(black_line_length)])
                if verbose:
                    cmd.append("--verbose")
                cmd.append(file_path)
                
            elif formatter == "yapf":
                cmd = ["yapf"]
                if not check_mode:
                    cmd.append("--in-place")
                if verbose:
                    cmd.append("--verbose")
                cmd.append(file_path)
                
            else:
                raise ValueError(f"Unsupported formatter: {formatter}")
            
            # Run the formatter
            process = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False
            )
            
            formatter_result["output"] = process.stdout
            formatter_result["error"] = process.stderr
            
            # Check if the file was modified or would be modified
            if process.returncode == 0:
                formatter_result["success"] = True
                # In check mode, no modifications means exit code 0
                formatter_result["modified"] = False
            elif process.returncode == 1 and check_mode:
                # In check mode, exit code 1 means the file would be modified
                formatter_result["success"] = True
                formatter_result["modified"] = True
            else:
                # Any other exit code is an error
                formatter_result["success"] = False
                result["success"] = False
                result["errors"].append(f"{formatter} failed: {process.stderr}")
                
        except Exception as e:
            formatter_result["success"] = False
            formatter_result["error"] = str(e)
            result["success"] = False
            result["errors"].append(f"{formatter} exception: {str(e)}")
        
        # Add results for this formatter
        result["formatters"][formatter] = formatter_result
    
    # Record duration
    result["duration"] = time.time() - start_time
    
    return result

def aggregate_format_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate formatting results from multiple files."""
    aggregate = {
        "total_files": len(results),
        "successful_files": 0,
        "failed_files": 0,
        "modified_files": 0,
        "would_modify_files": 0,
        "total_duration": 0,
        "formatter_stats": {},
        "file_results": results,
    }
    
    # Initialize formatter stats
    formatters_used = set()
    for result in results:
        formatters_used.update(result["formatters"].keys())
    
    for formatter in formatters_used:
        aggregate["formatter_stats"][formatter] = {
            "successful": 0,
            "failed": 0,
            "modified": 0,
            "would_modify": 0,
        }
    
    # Process results
    for result in results:
        if result["success"]:
            aggregate["successful_files"] += 1
        else:
            aggregate["failed_files"] += 1
        
        aggregate["total_duration"] += result["duration"]
        
        file_modified = False
        for formatter, formatter_result in result["formatters"].items():
            stats = aggregate["formatter_stats"][formatter]
            
            if formatter_result["success"]:
                stats["successful"] += 1
            else:
                stats["failed"] += 1
            
            if formatter_result["modified"]:
                if "check" in result and result["check"]:
                    stats["would_modify"] += 1
                    file_modified = True
                else:
                    stats["modified"] += 1
                    file_modified = True
        
        if file_modified:
            if "check" in result and result["check"]:
                aggregate["would_modify_files"] += 1
            else:
                aggregate["modified_files"] += 1
    
    return aggregate

def write_results_to_file(results: Dict[str, Any], output_file: str) -> None:
    """Write formatting results to a JSON file."""
    try:
        with open(output_file, "w") as f:
            json.dump(results, f, indent=2)
        logger.info(f"Formatting results written to {output_file}")
    except Exception as e:
        logger.error(f"Failed to write results to {output_file}: {e}")

def print_summary(results: Dict[str, Any], check_mode: bool) -> None:
    """Print a summary of the formatting results."""
    logger.info("\nFormatting Summary:")
    logger.info("------------------")
    logger.info(f"Total files processed: {results['total_files']}")
    logger.info(f"Successfully processed files: {results['successful_files']}")
    logger.info(f"Failed files: {results['failed_files']}")
    
    if check_mode:
        logger.info(f"Files that would be modified: {results['would_modify_files']}")
    else:
        logger.info(f"Files modified: {results['modified_files']}")
    
    logger.info(f"Total duration: {results['total_duration']:.2f} seconds")
    
    logger.info("\nFormatter Stats:")
    for formatter, stats in results["formatter_stats"].items():
        logger.info(f"  {formatter}:")
        logger.info(f"    Successful: {stats['successful']}")
        logger.info(f"    Failed: {stats['failed']}")
        if check_mode:
            logger.info(f"    Would modify: {stats['would_modify']}")
        else:
            logger.info(f"    Modified: {stats['modified']}")
    
    # Print files with errors if any
    if results['failed_files'] > 0:
        logger.info("\nFiles with errors:")
        for file_result in results['file_results']:
            if not file_result['success']:
                logger.info(f"  {file_result['file']}:")
                for error in file_result['errors']:
                    logger.info(f"    {error}")

def main() -> int:
    """Main entry point for the formatting script."""
    args = parse_args()
    
    if args.debug:
        logger.setLevel(logging.DEBUG)
    
    # Convert directory to absolute path
    directory = os.path.abspath(args.directory)
    
    if not os.path.isdir(directory):
        logger.error(f"Directory {directory} does not exist or is not a directory")
        return 1
    
    # Parse formatters and exclude patterns
    formatters = args.formatters.split(",") if args.formatters else ["black", "isort"]
    exclude_patterns = args.exclude.split(",") if args.exclude else []
    
    # Initialize Ray
    try:
        ray.init(address=args.ray_address)
        logger.info(f"Connected to Ray cluster at {args.ray_address}")
        
        # Get cluster resources for logging
        resources = get_cluster_resources()
        logger.info(f"Cluster resources: {resources}")
        
    except Exception as e:
        logger.error(f"Failed to connect to Ray cluster: {e}")
        return 1
    
    try:
        # Find Python files
        python_files = find_python_files(directory, args.include, exclude_patterns)
        
        if not python_files:
            logger.info("No Python files found. Exiting.")
            return 0
        
        # Run formatters in parallel
        format_tasks = [
            format_file.remote(
                file_path,
                formatters,
                args.check,
                args.black_line_length,
                args.isort_profile,
                args.verbose
            )
            for file_path in python_files
        ]
        
        # Get results
        format_results = ray.get(format_tasks)
        
        # Aggregate results
        aggregated_results = aggregate_format_results(format_results)
        aggregated_results["check"] = args.check
        
        # Print summary
        print_summary(aggregated_results, args.check)
        
        # Write results to file if specified
        if args.output:
            write_results_to_file(aggregated_results, args.output)
        
        # Return appropriate exit code
        if aggregated_results["failed_files"] > 0:
            logger.info("Some files failed formatting")
            return 1
        elif args.check and aggregated_results["would_modify_files"] > 0:
            logger.info("Some files would be modified")
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
    sys.exit(main())
