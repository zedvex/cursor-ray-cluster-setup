#!/usr/bin/env python3
"""
Script to run code linting in parallel using the Ray cluster.
Supports multiple linters including flake8, pylint, mypy, and bandit.
"""

import os
import sys
import time
import argparse
import logging
import json
import subprocess
from typing import List, Dict, Any, Optional, Tuple
import ray

# Add the project root to the Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, project_root)

from ray_tasks.task_manager import distribute_tasks
from ray_tasks.error_handling import retry

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
def lint_file(
    file_path: str,
    use_flake8: bool = True,
    use_pylint: bool = False,
    use_mypy: bool = False,
    use_bandit: bool = False,
    flake8_args: Optional[List[str]] = None,
    pylint_args: Optional[List[str]] = None,
    mypy_args: Optional[List[str]] = None,
    bandit_args: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Run linters on a single Python file
    
    Args:
        file_path: Path to the file to lint
        use_flake8: Whether to use flake8 linter
        use_pylint: Whether to use pylint linter
        use_mypy: Whether to use mypy linter
        use_bandit: Whether to use bandit linter
        flake8_args: Additional arguments for flake8
        pylint_args: Additional arguments for pylint
        mypy_args: Additional arguments for mypy
        bandit_args: Additional arguments for bandit
        
    Returns:
        Dictionary with linting results
    """
    result = {
        "file_path": file_path,
        "linters_used": [],
        "issues": [],
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
    
    # Run flake8
    if use_flake8:
        try:
            result["linters_used"].append("flake8")
            
            # Default flake8 args if none provided
            if flake8_args is None:
                flake8_args = ["--max-line-length=100"]
            
            command = ["flake8"] + flake8_args + [file_path]
            returncode, output = run_command(command)
            
            # Parse flake8 output (format: 'file:line:col: code message')
            if output.strip():
                for line in output.splitlines():
                    if not line.strip():
                        continue
                    
                    parts = line.split(':', 3)
                    if len(parts) < 4:
                        continue
                    
                    file_part, line_part, col_part, message_part = parts
                    
                    # Extract code and message
                    code_message = message_part.strip().split(' ', 1)
                    if len(code_message) == 2:
                        code, message = code_message
                    else:
                        code, message = code_message[0], ""
                    
                    result["issues"].append({
                        "linter": "flake8",
                        "file": file_path,
                        "line": int(line_part),
                        "column": int(col_part),
                        "code": code,
                        "message": message,
                        "severity": "warning"  # flake8 doesn't have severity levels
                    })
        except Exception as e:
            result["issues"].append({
                "linter": "flake8",
                "file": file_path,
                "line": 0,
                "column": 0,
                "code": "E999",
                "message": f"Error running flake8: {str(e)}",
                "severity": "error"
            })
    
    # Run pylint
    if use_pylint:
        try:
            result["linters_used"].append("pylint")
            
            # Default pylint args if none provided
            if pylint_args is None:
                pylint_args = ["--output-format=json"]
            elif "--output-format=json" not in pylint_args and all(not arg.startswith("--output-format=") for arg in pylint_args):
                pylint_args.append("--output-format=json")
            
            command = ["pylint"] + pylint_args + [file_path]
            returncode, output = run_command(command)
            
            # Parse pylint JSON output
            if output.strip():
                try:
                    pylint_issues = json.loads(output)
                    
                    # Map pylint severity to standardized severity
                    severity_map = {
                        "convention": "info",
                        "refactor": "info",
                        "warning": "warning",
                        "error": "error",
                        "fatal": "error"
                    }
                    
                    for issue in pylint_issues:
                        result["issues"].append({
                            "linter": "pylint",
                            "file": file_path,
                            "line": issue.get("line", 0),
                            "column": issue.get("column", 0),
                            "code": issue.get("symbol", issue.get("message-id", "")),
                            "message": issue.get("message", ""),
                            "severity": severity_map.get(issue.get("type", "warning"), "warning")
                        })
                except json.JSONDecodeError:
                    # Fall back to parsing text output
                    for line in output.splitlines():
                        if file_path in line and ':' in line:
                            parts = line.split(':', 2)
                            if len(parts) >= 3:
                                try:
                                    result["issues"].append({
                                        "linter": "pylint",
                                        "file": file_path,
                                        "line": int(parts[1]),
                                        "column": 0,
                                        "code": "",
                                        "message": parts[2].strip(),
                                        "severity": "warning"
                                    })
                                except (ValueError, IndexError):
                                    pass
        except Exception as e:
            result["issues"].append({
                "linter": "pylint",
                "file": file_path,
                "line": 0,
                "column": 0,
                "code": "E0000",
                "message": f"Error running pylint: {str(e)}",
                "severity": "error"
            })
    
    # Run mypy
    if use_mypy:
        try:
            result["linters_used"].append("mypy")
            
            # Default mypy args if none provided
            if mypy_args is None:
                mypy_args = ["--no-incremental", "--show-column-numbers"]
            
            command = ["mypy"] + mypy_args + [file_path]
            returncode, output = run_command(command)
            
            # Parse mypy output (format: 'file:line: error: message')
            if output.strip():
                for line in output.splitlines():
                    if not line.strip() or file_path not in line:
                        continue
                    
                    try:
                        # Split at the first colon to get file
                        file_rest = line.split(':', 1)
                        if len(file_rest) < 2:
                            continue
                        
                        # Split the rest at the first colon to get line
                        line_rest = file_rest[1].split(':', 1)
                        if len(line_rest) < 2:
                            continue
                        
                        line_num = int(line_rest[0])
                        
                        # Check if column number is specified
                        col_rest = line_rest[1].split(':', 1)
                        if len(col_rest) >= 2 and col_rest[0].strip().isdigit():
                            col_num = int(col_rest[0])
                            message_part = col_rest[1]
                        else:
                            col_num = 0
                            message_part = line_rest[1]
                        
                        # Determine severity and message
                        severity = "error" if "error:" in message_part else "warning"
                        message = message_part.split(':', 1)[1].strip() if ':' in message_part else message_part.strip()
                        
                        result["issues"].append({
                            "linter": "mypy",
                            "file": file_path,
                            "line": line_num,
                            "column": col_num,
                            "code": "mypy",
                            "message": message,
                            "severity": severity
                        })
                    except (ValueError, IndexError):
                        pass
        except Exception as e:
            result["issues"].append({
                "linter": "mypy",
                "file": file_path,
                "line": 0,
                "column": 0,
                "code": "mypy",
                "message": f"Error running mypy: {str(e)}",
                "severity": "error"
            })
    
    # Run bandit
    if use_bandit:
        try:
            result["linters_used"].append("bandit")
            
            # Default bandit args if none provided
            if bandit_args is None:
                bandit_args = ["-f", "json"]
            elif "-f" not in bandit_args and "--format" not in bandit_args:
                bandit_args.extend(["-f", "json"])
            
            command = ["bandit"] + bandit_args + [file_path]
            returncode, output = run_command(command)
            
            # Parse bandit JSON output
            if output.strip():
                try:
                    bandit_result = json.loads(output)
                    
                    # Map bandit severity to standardized severity
                    severity_map = {
                        "LOW": "info",
                        "MEDIUM": "warning",
                        "HIGH": "error"
                    }
                    
                    for result_item in bandit_result.get("results", []):
                        result["issues"].append({
                            "linter": "bandit",
                            "file": file_path,
                            "line": result_item.get("line_number", 0),
                            "column": 0,
                            "code": result_item.get("test_id", ""),
                            "message": result_item.get("issue_text", ""),
                            "severity": severity_map.get(result_item.get("issue_severity", "MEDIUM"), "warning")
                        })
                except json.JSONDecodeError:
                    # Fall back to parsing text output if JSON fails
                    pass
        except Exception as e:
            result["issues"].append({
                "linter": "bandit",
                "file": file_path,
                "line": 0,
                "column": 0,
                "code": "B000",
                "message": f"Error running bandit: {str(e)}",
                "severity": "error"
            })
    
    return result

@ray.remote
def aggregate_lint_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Aggregate linting results from multiple files
    
    Args:
        results: List of linting results
        
    Returns:
        Dictionary with aggregated linting results
    """
    # Collect all issues
    all_issues = []
    
    # Count issues by severity and linter
    issue_counts = {
        "total": 0,
        "by_severity": {
            "error": 0,
            "warning": 0,
            "info": 0
        },
        "by_linter": {
            "flake8": 0,
            "pylint": 0,
            "mypy": 0,
            "bandit": 0
        },
        "by_file": {}
    }
    
    # Process each file's results
    for file_result in results:
        file_path = file_result["file_path"]
        file_issues = file_result.get("issues", [])
        
        # Add file issues to all issues
        all_issues.extend(file_issues)
        
        # Count issues for this file
        file_issue_count = len(file_issues)
        issue_counts["total"] += file_issue_count
        
        # Count by file
        issue_counts["by_file"][file_path] = file_issue_count
        
        # Count by severity and linter
        for issue in file_issues:
            severity = issue.get("severity", "warning")
            linter = issue.get("linter", "unknown")
            
            # Update severity counts
            if severity in issue_counts["by_severity"]:
                issue_counts["by_severity"][severity] += 1
            
            # Update linter counts
            if linter in issue_counts["by_linter"]:
                issue_counts["by_linter"][linter] += 1
    
    # Sort issues by severity and line number
    def issue_sort_key(issue):
        # Order: error, warning, info
        severity_order = {"error": 0, "warning": 1, "info": 2}
        return (
            issue["file"],
            severity_order.get(issue.get("severity", "warning"), 1),
            issue.get("line", 0)
        )
    
    all_issues.sort(key=issue_sort_key)
    
    # Most common issues
    issue_types = {}
    for issue in all_issues:
        code = issue.get("code", "")
        linter = issue.get("linter", "")
        key = f"{linter}:{code}" if code else linter
        
        if key not in issue_types:
            issue_types[key] = {
                "count": 0,
                "linter": linter,
                "code": code,
                "sample_message": issue.get("message", "")
            }
        
        issue_types[key]["count"] += 1
    
    # Sort issue types by count
    sorted_issue_types = sorted(
        issue_types.values(),
        key=lambda x: x["count"],
        reverse=True
    )
    
    # Files with the most issues
    files_by_issues = sorted(
        issue_counts["by_file"].items(),
        key=lambda x: x[1],
        reverse=True
    )
    
    # Create overall summary
    summary = {
        "total_files": len(results),
        "files_with_issues": sum(1 for count in issue_counts["by_file"].values() if count > 0),
        "total_issues": issue_counts["total"],
        "issue_counts": issue_counts,
        "common_issues": sorted_issue_types[:20],  # Top 20 most common issues
        "files_most_issues": [
            {"file": file, "issues": count}
            for file, count in files_by_issues[:20]  # Top 20 files with most issues
        ],
        "all_issues": all_issues
    }
    
    return summary

def run_linters(
    directory: str,
    batch_size: int = 5,
    exclude_dirs: Optional[List[str]] = None,
    use_flake8: bool = True,
    use_pylint: bool = False,
    use_mypy: bool = False,
    use_bandit: bool = False,
    flake8_args: Optional[List[str]] = None,
    pylint_args: Optional[List[str]] = None,
    mypy_args: Optional[List[str]] = None,
    bandit_args: Optional[List[str]] = None,
    output_file: Optional[str] = None
) -> Dict[str, Any]:
    """
    Run linters on all Python files in the specified directory using Ray
    
    Args:
        directory: Directory containing Python files to lint
        batch_size: Number of files to process in each batch
        exclude_dirs: Directories to exclude
        use_flake8: Whether to use flake8 linter
        use_pylint: Whether to use pylint linter
        use_mypy: Whether to use mypy linter
        use_bandit: Whether to use bandit linter
        flake8_args: Additional arguments for flake8
        pylint_args: Additional arguments for pylint
        mypy_args: Additional arguments for mypy
        bandit_args: Additional arguments for bandit
        output_file: File to write results to
        
    Returns:
        Dictionary with linting results
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
    
    logger.info(f"Found {len(python_files)} Python files to lint")
    
    # Print which linters are being used
    linters_used = []
    if use_flake8:
        linters_used.append("flake8")
    if use_pylint:
        linters_used.append("pylint")
    if use_mypy:
        linters_used.append("mypy")
    if use_bandit:
        linters_used.append("bandit")
    
    logger.info(f"Using linters: {', '.join(linters_used)}")
    
    # Function to track progress
    def show_progress(current, total):
        logger.info(f"Progress: {current}/{total} files ({current/total*100:.1f}%)")
    
    # Use task manager to distribute linting tasks
    results = distribute_tasks(
        task_func=lint_file,
        items=python_files,
        task_type="linting",
        batch_size=batch_size,
        progress_callback=show_progress,
        retry_attempts=2,
        # Pass linting parameters
        remote_args={
            "use_flake8": use_flake8,
            "use_pylint": use_pylint,
            "use_mypy": use_mypy,
            "use_bandit": use_bandit,
            "flake8_args": flake8_args,
            "pylint_args": pylint_args,
            "mypy_args": mypy_args,
            "bandit_args": bandit_args
        }
    )
    
    # Aggregate results
    logger.info("Aggregating linting results...")
    aggregated = ray.get(aggregate_lint_results.remote(results))
    
    # Add execution time
    elapsed_time = time.time() - start_time
    aggregated["execution_time"] = elapsed_time
    
    # Write results to file if specified
    if output_file:
        with open(output_file, 'w') as f:
            json.dump(aggregated, f, indent=2)
        logger.info(f"Results written to {output_file}")
    
    return aggregated

def print_summary(results: Dict[str, Any], verbose: bool = False) -> None:
    """
    Print a summary of the linting results
    
    Args:
        results: Dictionary with linting results
        verbose: Whether to print verbose output
    """
    print("\n" + "="*60)
    print("LINTING SUMMARY")
    print("="*60)
    
    total_files = results["total_files"]
    files_with_issues = results["files_with_issues"]
    total_issues = results["total_issues"]
    
    print(f"Files processed:   {total_files}")
    print(f"Files with issues: {files_with_issues} ({files_with_issues/total_files*100:.1f}% of files)")
    print(f"Total issues:      {total_issues}")
    
    # Print issue counts by severity
    by_severity = results["issue_counts"]["by_severity"]
    print("\nIssues by severity:")
    print(f"  Errors:   {by_severity.get('error', 0)}")
    print(f"  Warnings: {by_severity.get('warning', 0)}")
    print(f"  Info:     {by_severity.get('info', 0)}")
    
    # Print issue counts by linter
    by_linter = results["issue_counts"]["by_linter"]
    print("\nIssues by linter:")
    for linter, count in by_linter.items():
        if count > 0:
            print(f"  {linter}: {count}")
    
    # Print most common issues
    if results.get("common_issues"):
        print("\nMost common issues:")
        for i, issue in enumerate(results["common_issues"][:10]):  # Top 10
            linter = issue["linter"]
            code = issue["code"]
            msg = issue["sample_message"]
            count = issue["count"]
            
            issue_id = f"{linter}:{code}" if code else linter
            print(f"  {i+1}. {issue_id} - {count} occurrences")
            print(f"     Sample: {msg}")
    
    # Print files with most issues
    if results.get("files_most_issues"):
        print("\nFiles with most issues:")
        for i, file_info in enumerate(results["files_most_issues"][:5]):  # Top 5
            file = file_info["file"]
            count = file_info["issues"]
            print(f"  {i+1}. {file}: {count} issues")
    
    # Print detailed issues in verbose mode
    if verbose and results.get("all_issues"):
        print("\nDetailed issues:")
        current_file = ""
        
        for issue in results["all_issues"]:
            file = issue.get("file", "")
            line = issue.get("line", 0)
            col = issue.get("column", 0)
            linter = issue.get("linter", "")
            code = issue.get("code", "")
            message = issue.get("message", "")
            severity = issue.get("severity", "warning")
            
            # Print file header when changing files
            if file != current_file:
                print(f"\n{file}:")
                current_file = file
            
            # Format position
            pos = f"{line}"
            if col > 0:
                pos += f":{col}"
            
            # Format issue ID
            issue_id = f"{linter}:{code}" if code else linter
            
            # Format severity
            severity_symbol = {
                "error": "✖",
                "warning": "⚠",
                "info": "ℹ"
            }.get(severity, "⚠")
            
            print(f"  {severity_symbol} {pos} - {issue_id}: {message}")
    
    # Print execution time
    if "execution_time" in results:
        print(f"\nExecution time: {results['execution_time']:.2f} seconds")
    
    print("="*60)

def main():
    parser = argparse.ArgumentParser(description="Lint Python code using Ray")
    parser.add_argument("--dir", "-d", type=str, default=".", help="Directory to lint (default: current directory)")
    parser.add_argument("--batch-size", "-b", type=int, default=5, help="Batch size for processing (default: 5)")
    parser.add_argument("--exclude", "-e", type=str, nargs="+", help="Directories to exclude")
    parser.add_argument("--output-file", "-o", type=str, help="File to write results to")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print verbose output")
    
    # Linter selection arguments
    parser.add_argument("--flake8", action="store_true", help="Use flake8 linter (default)")
    parser.add_argument("--pylint", action="store_true", help="Use pylint linter")
    parser.add_argument("--mypy", action="store_true", help="Use mypy linter")
    parser.add_argument("--bandit", action="store_true", help="Use bandit linter")
    parser.add_argument("--all", action="store_true", help="Use all supported linters")
    parser.add_argument("--no-flake8", action="store_true", help="Don't use flake8 linter")
    
    # Linter-specific arguments
    parser.add_argument("--flake8-args", type=str, nargs="+", help="Additional arguments for flake8")
    parser.add_argument("--pylint-args", type=str, nargs="+", help="Additional arguments for pylint")
    parser.add_argument("--mypy-args", type=str, nargs="+", help="Additional arguments for mypy")
    parser.add_argument("--bandit-args", type=str, nargs="+", help="Additional arguments for bandit")
    
    args = parser.parse_args()
    
    # Determine which linters to use
    # Default to flake8 unless explicitly disabled or others are enabled
    use_flake8 = True
    use_pylint = False
    use_mypy = False
    use_bandit = False
    
    # Override with explicit enables
    if args.pylint:
        use_pylint = True
    if args.mypy:
        use_mypy = True
    if args.bandit:
        use_bandit = True
    if args.flake8:
        use_flake8 = True
    
    # If any linters are explicitly enabled, disable flake8 by default
    if not args.flake8 and (args.pylint or args.mypy or args.bandit):
        use_flake8 = False
    
    # Enable all linters if --all is specified
    if args.all:
        use_flake8 = True
        use_pylint = True
        use_mypy = True
        use_bandit = True
    
    # Explicit disabling overrides enabling
    if args.no_flake8:
        use_flake8 = False
    
    # Run linters
    try:
        logger.info("Starting linting run...")
        results = run_linters(
            directory=args.dir,
            batch_size=args.batch_size,
            exclude_dirs=args.exclude,
            use_flake8=use_flake8,
            use_pylint=use_pylint,
            use_mypy=use_mypy,
            use_bandit=use_bandit,
            flake8_args=args.flake8_args,
            pylint_args=args.pylint_args,
            mypy_args=args.mypy_args,
            bandit_args=args.bandit_args,
            output_file=args.output_file
        )
        
        # Print summary
        print_summary(results, verbose=args.verbose)
        
        # Determine exit code based on presence of errors
        error_count = results["issue_counts"]["by_severity"].get("error", 0)
        exit_code = 1 if error_count > 0 else 0
        
        # Shutdown Ray
        ray.shutdown()
        
        # Exit with appropriate code
        sys.exit(exit_code)
        
    except Exception as e:
        logger.error(f"Error running linter: {str(e)}")
        ray.shutdown()
        sys.exit(1)

if __name__ == "__main__":
    main()
