#!/usr/bin/env python3
"""
Script to run code formatting in parallel using the Ray cluster.
Supports multiple formatters including black, isort, and autopep8.
"""

import os
import sys
import time
import argparse
import logging
import json
from typing import List, Dict, Any, Optional, Tuple
import ray

# Add the project root to the Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, project_root)

from ray_tasks.task_manager import distribute_tasks

# Configure logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def find_python_files(directory: str, exclude_dirs: Optional[List[str]] = None) -> List[str]:
    """
    Recursively find all Python files in a directory
    
    Args:
        directory: The root directory to search
        exclude_dirs: List of directory names to exclude
        
    Returns:
        List of paths to Python files
    """
    if exclude_dirs is None:
        exclude_dirs = ["venv", "env", ".git", "__pycache__", "build", "dist"]
    
    python_files = []
    
    for root, dirs, files in os.walk(directory):
        # Skip excluded directories
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        
        for file in files:
            if file.endswith(".py"):
                python_files.append(os.path.join(root, file))
    
    return python_files

@ray.remote
def format_file(
    file_path: str,
    use_black: bool = True,
    use_isort: bool = False,
    use_autopep8: bool = False,
    black_args: Optional[List[str]] = None,
    isort_args: Optional[List[str]] = None,
    autopep8_args: Optional[List[str]] = None,
    check_only: bool = False
) -> Dict[str, Any]:
    """
    Format a single Python file using the specified formatters
    
    Args:
        file_path: Path to the file to format
        use_black: Whether to use black formatter
        use_isort: Whether to use isort formatter
        use_autopep8: Whether to use autopep8 formatter
        black_args: Additional arguments for black
        isort_args: Additional arguments for isort
        autopep8_args: Additional arguments for autopep8
        check_only: Only check if formatting is needed without making changes
        
    Returns:
        Dictionary with formatting results
    """
    import subprocess
    from difflib import unified_diff
    
    result = {
        "file_path": file_path,
        "formatters_used": [],
        "black": {"status": "not_run", "diff": [], "would_change": False},
        "isort": {"status": "not_run", "diff": [], "would_change": False},
        "autopep8": {"status": "not_run", "diff": [], "would_change": False},
        "changes_made": False,
        "error": None
    }
    
    # Check if file exists
    if not os.path.exists(file_path):
        result["error"] = f"File not found: {file_path}"
        return result
    
    # Helper function to run a command and capture its output
    def run_command(command: List[str]) -> Tuple[int, str]:
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            stdout, stderr = process.communicate()
            return process.returncode, stdout + stderr
        except Exception as e:
            return 1, str(e)
    
    # Helper function to get file contents
    def get_file_content(path: str) -> str:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    
    # Get original content before formatting
    original_content = get_file_content(file_path)
    
    # Run black
    if use_black:
        try:
            result["formatters_used"].append("black")
            
            # Default black args if none provided
            if black_args is None:
                black_args = ["--line-length=88"]
            
            # Always add the check flag if check_only is True
            if check_only:
                if "--check" not in black_args:
                    black_args.append("--check")
                if "--diff" not in black_args:
                    black_args.append("--diff")
            
            command = ["black"] + black_args + [file_path]
            returncode, output = run_command(command)
            
            if check_only:
                # Parse black check output
                result["black"]["status"] = "unchanged" if returncode == 0 else "would_change"
                result["black"]["would_change"] = returncode != 0
                
                # Store diff if available
                if "--diff" in black_args and output:
                    result["black"]["diff"] = output.splitlines()
            else:
                result["black"]["status"] = "success" if returncode == 0 else "error"
                
                # Check if changes were made by comparing file content
                new_content = get_file_content(file_path)
                if original_content != new_content:
                    result["changes_made"] = True
                    result["black"]["would_change"] = True
                    
                    # Generate diff
                    diff = unified_diff(
                        original_content.splitlines(),
                        new_content.splitlines(),
                        fromfile=f"{file_path} (original)",
                        tofile=f"{file_path} (formatted)",
                        lineterm=""
                    )
                    result["black"]["diff"] = list(diff)
                    
                    # Update original content for next formatter
                    original_content = new_content
                
        except Exception as e:
            result["black"]["status"] = "error"
            result["black"]["error"] = str(e)
    
    # Run isort
    if use_isort:
        try:
            result["formatters_used"].append("isort")
            
            # Default isort args if none provided
            if isort_args is None:
                isort_args = ["--profile=black"]  # Compatible with black
            
            # Always add the check flag if check_only is True
            if check_only:
                if "--check" not in isort_args:
                    isort_args.append("--check")
                if "--diff" not in isort_args:
                    isort_args.append("--diff")
            
            command = ["isort"] + isort_args + [file_path]
            returncode, output = run_command(command)
            
            if check_only:
                # Parse isort check output
                result["isort"]["status"] = "unchanged" if returncode == 0 else "would_change"
                result["isort"]["would_change"] = returncode != 0
                
                # Store diff if available
                if "--diff" in isort_args and output:
                    result["isort"]["diff"] = output.splitlines()
            else:
                result["isort"]["status"] = "success" if returncode == 0 else "error"
                
                # Check if changes were made by comparing file content
                new_content = get_file_content(file_path)
                if original_content != new_content:
                    result["changes_made"] = True
                    result["isort"]["would_change"] = True
                    
                    # Generate diff
                    diff = unified_diff(
                        original_content.splitlines(),
                        new_content.splitlines(),
                        fromfile=f"{file_path} (original)",
                        tofile=f"{file_path} (formatted)",
                        lineterm=""
                    )
                    result["isort"]["diff"] = list(diff)
                    
                    # Update original content for next formatter
                    original_content = new_content
                
        except Exception as e:
            result["isort"]["status"] = "error"
            result["isort"]["error"] = str(e)
    
    # Run autopep8
    if use_autopep8:
        try:
            result["formatters_used"].append("autopep8")
            
            # Default autopep8 args if none provided
            if autopep8_args is None:
                autopep8_args = ["--aggressive", "--aggressive"]
            
            # Add the check flag if check_only is True
            if check_only:
                if "--diff" not in autopep8_args:
                    autopep8_args.append("--diff")
            else:
                if "--in-place" not in autopep8_args:
                    autopep8_args.append("--in-place")
            
            command = ["autopep8"] + autopep8_args + [file_path]
            returncode, output = run_command(command)
            
            if check_only:
                # Parse autopep8 check output (diff is always generated)
                has_changes = bool(output.strip())
                result["autopep8"]["status"] = "unchanged" if not has_changes else "would_change"
                result["autopep8"]["would_change"] = has_changes
                
                # Store diff if available
                if has_changes:
                    result["autopep8"]["diff"] = output.splitlines()
            else:
                result["autopep8"]["status"] = "success" if returncode == 0 else "error"
                
                # Check if changes were made by comparing file content
                new_content = get_file_content(file_path)
                if original_content != new_content:
                    result["changes_made"] = True
                    result["autopep8"]["would_change"] = True
                    
                    # Generate diff
                    diff = unified_diff(
                        original_content.splitlines(),
                        new_content.splitlines(),
                        fromfile=f"{file_path} (original)",
                        tofile=f"{file_path} (formatted)",
                        lineterm=""
                    )
                    result["autopep8"]["diff"] = list(diff)
                
        except Exception as e:
            result["autopep8"]["status"] = "error"
            result["autopep8"]["error"] = str(e)
    
    return result

@ray.remote
def aggregate_format_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Aggregate formatting results from multiple files
    
    Args:
        results: List of formatting results
        
    Returns:
        Dictionary with aggregated formatting results
    """
    total_files = len(results)
    files_changed = sum(1 for r in results if r.get("changes_made", False))
    
    # Count by formatter
    formatter_counts = {
        "black": {"success": 0, "error": 0, "unchanged": 0, "would_change": 0, "not_run": 0},
        "isort": {"success": 0, "error": 0, "unchanged": 0, "would_change": 0, "not_run": 0},
        "autopep8": {"success": 0, "error": 0, "unchanged": 0, "would_change": 0, "not_run": 0}
    }
    
    # Files that would be changed by formatter
    files_with_changes = {
        "black": [],
        "isort": [],
        "autopep8": []
    }
    
    # Process each file's results
    for r in results:
        for formatter in ["black", "isort", "autopep8"]:
            formatter_result = r.get(formatter, {})
            status = formatter_result.get("status", "not_run")
            
            # Count result by status
            formatter_counts[formatter][status] += 1
            
            # If changes would be made, add to files with changes
            if formatter_result.get("would_change", False):
                files_with_changes[formatter].append({
                    "file_path": r["file_path"],
                    "diff": formatter_result.get("diff", [])
                })
    
    # Error files (files that couldn't be processed)
    error_files = [
        {"file_path": r["file_path"], "error": r["error"]}
        for r in results
        if r.get("error") is not None
    ]
    
    # Create overall summary
    summary = {
        "total_files": total_files,
        "files_changed": files_changed,
        "unchanged_files": total_files - files_changed,
        "formatter_counts": formatter_counts,
        "files_with_changes": files_with_changes,
        "error_files": error_files
    }
    
    return summary

def format_directory(
    directory: str,
    batch_size: int = 10,
    exclude_dirs: Optional[List[str]] = None,
    use_black: bool = True,
    use_isort: bool = True,
    use_autopep8: bool = False,
    black_args: Optional[List[str]] = None,
    isort_args: Optional[List[str]] = None,
    autopep8_args: Optional[List[str]] = None,
    check_only: bool = False,
    output_file: Optional[str] = None
) -> Dict[str, Any]:
    """
    Format all Python files in the specified directory using Ray
    
    Args:
        directory: Directory containing Python files to format
        batch_size: Number of files to process in each batch
        exclude_dirs: Directories to exclude
        use_black: Whether to use black formatter
        use_isort: Whether to use isort formatter
        use_autopep8: Whether to use autopep8 formatter
        black_args: Additional arguments for black
        isort_args: Additional arguments for isort
        autopep8_args: Additional arguments for autopep8
        check_only: Only check if formatting is needed without making changes
        output_file: File to write results to
        
    Returns:
        Dictionary with formatting results
    """
    start_time = time.time()
    
    # Initialize Ray if not already running
    if not ray.is_initialized():
        try:
            ray.init(address="auto", ignore_reinit_error=True)
            logger.info("Connected to existing Ray cluster")
        except ConnectionError:
            ray.init(ignore_reinit_error=True)
            logger.info("Started new local Ray instance")
    
    # Find Python files
    logger.info(f"Finding Python files in {directory}...")
    python_files = find_python_files(directory, exclude_dirs)
    
    if not python_files:
        logger.warning(f"No Python files found in {directory}")
        return {"status": "error", "message": "No Python files found"}
    
    logger.info(f"Found {len(python_files)} Python files to format")
    
    # Print which formatters are being used
    formatters_used = []
    if use_black:
        formatters_used.append("black")
    if use_isort:
        formatters_used.append("isort")
    if use_autopep8:
        formatters_used.append("autopep8")
    
    mode = "check" if check_only else "format"
    logger.info(f"Using formatters: {', '.join(formatters_used)} (mode: {mode})")
    
    # Function to track progress
    def show_progress(current, total):
        logger.info(f"Progress: {current}/{total} files ({current/total*100:.1f}%)")
    
    # Use task manager to distribute formatting tasks
    results = distribute_tasks(
        task_func=format_file,
        items=python_files,
        task_type="formatting",
        batch_size=batch_size,
        progress_callback=show_progress,
        retry_attempts=2,
        memory_intensive=False,
        # Pass formatting parameters
        remote_args={
            "use_black": use_black,
            "use_isort": use_isort,
            "use_autopep8": use_autopep8,
            "black_args": black_args,
            "isort_args": isort_args,
            "autopep8_args": autopep8_args,
            "check_only": check_only,
        }
    )
    
    # Aggregate results
    logger.info("Aggregating formatting results...")
    aggregated = ray.get(aggregate_format_results.remote(results))
    
    # Add execution time
    elapsed_time = time.time() - start_time
    aggregated["execution_time"] = elapsed_time
    aggregated["check_only"] = check_only
    
    # Write results to file if specified
    if output_file:
        with open(output_file, 'w') as f:
            json.dump(aggregated, f, indent=2)
        logger.info(f"Results written to {output_file}")
    
    return aggregated

def print_summary(results: Dict[str, Any], show_diff: bool = False) -> None:
    """
    Print a summary of the formatting results
    
    Args:
        results: Dictionary with formatting results
        show_diff: Whether to show diffs for changes
    """
    print("\n" + "="*60)
    mode = "CHECK" if results.get("check_only", False) else "FORMAT"
    print(f"CODE {mode} SUMMARY")
    print("="*60)
    
    print(f"Files processed: {results['total_files']}")
    
    if results.get("check_only", False):
        # Check mode
        total_would_change = sum(
            len(results['files_with_changes'][f]) 
            for f in ['black', 'isort', 'autopep8']
        )
        print(f"Files that would be changed: {total_would_change}")
        print(f"Files that would be unchanged: {results['total_files'] - total_would_change}")
    else:
        # Format mode
        print(f"Files changed: {results['files_changed']}")
        print(f"Files unchanged: {results['unchanged_files']}")
    
    # Results by formatter
    for formatter in ["black", "isort", "autopep8"]:
        counts = results["formatter_counts"][formatter]
        total_run = sum(counts.values()) - counts["not_run"]
        
        if total_run > 0:  # Only show if formatter was run
            print(f"\n{formatter.upper()} results:")
            
            if results.get("check_only", False):
                # Check mode
                would_change = counts["would_change"]
                unchanged = counts["unchanged"]
                print(f"  Would change:  {would_change}")
                print(f"  Unchanged:     {unchanged}")
                change_percent = (would_change / total_run * 100) if total_run > 0 else 0
                print(f"  Change needed: {change_percent:.1f}% of files")
            else:
                # Format mode
                print(f"  Success:       {counts['success']}")
                print(f"  Unchanged:     {counts['unchanged']}")
            
            if counts["error"] > 0:
                print(f"  Errors:        {counts['error']}")
    
    # Print errors if any
    if results.get("error_files", []):
        print("\nFiles with errors:")
        for error_file in results["error_files"]:
            print(f"  {error_file['file_path']}: {error_file['error']}")
    
    # Print diffs if requested
    if show_diff:
        for formatter in ["black", "isort", "autopep8"]:
            files_with_changes = results["files_with_changes"][formatter]
            if files_with_changes:
                print(f"\n{formatter.upper()} changes:")
                
                for file_entry in files_with_changes:
                    file_path = file_entry["file_path"]
                    diff = file_entry["diff"]
                    
                    if diff:
                        print(f"\n  {file_path}:")
                        for line in diff:
                            # Colorize diff output
                            if line.startswith('+') and not line.startswith('+++'):
                                print(f"    \033[32m{line}\033[0m")  # Green for additions
                            elif line.startswith('-') and not line.startswith('---'):
                                print(f"    \033[31m{line}\033[0m")  # Red for deletions
                            else:
                                print(f"    {line}")
    
    # Print execution time
    if "execution_time" in results:
        print(f"\nExecution time: {results['execution_time']:.2f} seconds")
    
    print("="*60)

def main():
    parser = argparse.ArgumentParser(description="Format Python code using Ray")
    parser.add_argument("--dir", "-d", type=str, default=".", help="Directory to format (default: current directory)")
    parser.add_argument("--batch-size", "-b", type=int, default=10, help="Batch size for processing (default: 10)")
    parser.add_argument("--exclude", "-e", type=str, nargs="+", help="Directories to exclude")
    parser.add_argument("--output-file", "-o", type=str, help="File to write results to")
    parser.add_argument("--check", "-c", action="store_true", help="Check if formatting is needed without making changes")
    parser.add_argument("--diff", "-f", action="store_true", help="Show diff of formatting changes")
    
    # Formatter selection arguments
    parser.add_argument("--black", action="store_true", help="Use black formatter (default)")
    parser.add_argument("--isort", action="store_true", help="Use isort formatter (default)")
    parser.add_argument("--autopep8", action="store_true", help="Use autopep8 formatter")
    parser.add_argument("--no-black", action="store_true", help="Don't use black formatter")
    parser.add_argument("--no-isort", action="store_true", help="Don't use isort formatter")
    
    # Formatter-specific arguments
    parser.add_argument("--black-args", type=str, nargs="+", help="Additional arguments for black")
    parser.add_argument("--isort-args", type=str, nargs="+", help="Additional arguments for isort")
    parser.add_argument("--autopep8-args", type=str, nargs="+", help="Additional arguments for autopep8")
    
    args = parser.parse_args()
    
    # Determine which formatters to use
    # Default to black and isort unless explicitly disabled or others are enabled
    use_black = True
    use_isort = True
    use_autopep8 = args.autopep8
    
    # Override defaults based on arguments
    if args.black or args.isort:
        # If any formatters are explicitly enabled, disable defaults
        use_black = args.black
        use_isort = args.isort
    
    # Explicit disabling overrides enabling
    if args.no_black:
        use_black = False
    if args.no_isort:
        use_isort = False
    
    # Run formatter
    try:
        logger.info("Starting code formatting run...")
        results = format_directory(
            directory=args.dir,
            batch_size=args.batch_size,
            exclude_dirs=args.exclude,
            use_black=use_black,
            use_isort=use_isort,
            use_autopep8=use_autopep8,
            black_args=args.black_args,
            isort_args=args.isort_args,
            autopep8_args=args.autopep8_args,
            check_only=args.check,
            output_file=args.output_file
        )
        
        # Print summary
        print_summary(results, show_diff=args.diff)
        
        # Determine exit code
        # In check mode, exit with non-zero if any files would be changed
        # In format mode, exit with 0 (success) regardless of changes
        if args.check:
            total_would_change = sum(
                len(results['files_with_changes'][f]) 
                for f in ['black', 'isort', 'autopep8']
            )
            exit_code = 1 if total_would_change > 0 else 0
        else:
            exit_code = 0
        
        # Shutdown Ray
        ray.shutdown()
        
        # Exit with appropriate code
        sys.exit(exit_code)
        
    except Exception as e:
        logger.error(f"Error running formatter: {str(e)}")
        ray.shutdown()
        sys.exit(1)

if __name__ == "__main__":
    main()
