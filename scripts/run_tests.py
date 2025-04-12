#!/usr/bin/env python3
"""
Script to run tests in parallel using the Ray cluster.
Distributes test execution across the cluster and aggregates results.
"""

import os
import sys
import time
import argparse
import logging
import json
import subprocess
from typing import List, Dict, Any, Optional, Tuple, Set
import glob
import re
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

def find_test_files(
    directory: str,
    pattern: str = "test_*.py",
    exclude_dirs: Optional[List[str]] = None
) -> List[str]:
    """
    Find all test files in the given directory
    
    Args:
        directory: Directory to search for test files
        pattern: File pattern to match
        exclude_dirs: Directories to exclude
    
    Returns:
        List of test file paths
    """
    if exclude_dirs is None:
        exclude_dirs = ["venv", "env", ".git", "__pycache__", "build", "dist"]
    
    test_files = []
    
    for root, dirs, files in os.walk(directory):
        # Skip excluded directories
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        
        # Find test files matching the pattern
        for file in files:
            if glob.fnmatch.fnmatch(file, pattern):
                test_files.append(os.path.join(root, file))
    
    return test_files

@ray.remote
def run_test_file(
    file_path: str,
    pytest_args: Optional[List[str]] = None,
    verbose: bool = False,
    junit_xml: Optional[str] = None,
    coverage: bool = False
) -> Dict[str, Any]:
    """
    Run tests from a single file using pytest
    
    Args:
        file_path: Path to the test file
        pytest_args: Additional arguments for pytest
        verbose: Whether to run pytest in verbose mode
        junit_xml: Path to output JUnit XML report
        coverage: Whether to collect coverage data
    
    Returns:
        Dictionary with test results
    """
    import tempfile
    import xml.etree.ElementTree as ET
    
    result = {
        "file_path": file_path,
        "tests": [],
        "summary": {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "error": 0,
            "xfailed": 0,
            "xpassed": 0
        },
        "duration": 0,
        "error": None
    }
    
    # Check if file exists
    if not os.path.exists(file_path):
        result["error"] = f"File not found: {file_path}"
        return result
    
    try:
        # Create temp directory for test outputs
        with tempfile.TemporaryDirectory() as temp_dir:
            # Prepare XML report path
            xml_report_path = os.path.join(temp_dir, "report.xml")
            
            # Build pytest command
            cmd = ["pytest", file_path, "-v"]
            
            # Add XML report
            cmd.extend(["--junitxml", xml_report_path])
            
            # Add coverage if requested
            if coverage:
                cmd.extend(["--cov", os.path.dirname(file_path), "--cov-report", "term"])
            
            # Add any additional arguments
            if pytest_args:
                cmd.extend(pytest_args)
            
            # Run pytest
            start_time = time.time()
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            stdout, stderr = process.communicate()
            duration = time.time() - start_time
            
            # Parse output to get test results
            result["duration"] = duration
            result["output"] = stdout
            result["stderr"] = stderr if stderr else None
            
            # Parse XML report if it exists
            if os.path.exists(xml_report_path):
                try:
                    tree = ET.parse(xml_report_path)
                    root = tree.getroot()
                    
                    # Extract test cases
                    for testcase in root.findall(".//testcase"):
                        test_result = {
                            "name": testcase.get("name"),
                            "classname": testcase.get("classname"),
                            "file": file_path,
                            "time": float(testcase.get("time", 0)),
                            "status": "passed"  # Default status
                        }
                        
                        # Check for failure
                        failure = testcase.find("failure")
                        if failure is not None:
                            test_result["status"] = "failed"
                            test_result["message"] = failure.get("message")
                            test_result["detail"] = failure.text if failure.text else ""
                        
                        # Check for error
                        error = testcase.find("error")
                        if error is not None:
                            test_result["status"] = "error"
                            test_result["message"] = error.get("message")
                            test_result["detail"] = error.text if error.text else ""
                        
                        # Check for skipped
                        skipped = testcase.find("skipped")
                        if skipped is not None:
                            test_result["status"] = "skipped"
                            test_result["message"] = skipped.get("message")
                        
                        # Add to results
                        result["tests"].append(test_result)
                    
                    # Extract summary from testsuite element
                    testsuite = root.find(".//testsuite")
                    if testsuite is not None:
                        result["summary"]["total"] = int(testsuite.get("tests", 0))
                        result["summary"]["passed"] = (
                            result["summary"]["total"] - 
                            int(testsuite.get("failures", 0)) - 
                            int(testsuite.get("errors", 0)) - 
                            int(testsuite.get("skipped", 0))
                        )
                        result["summary"]["failed"] = int(testsuite.get("failures", 0))
                        result["summary"]["error"] = int(testsuite.get("errors", 0))
                        result["summary"]["skipped"] = int(testsuite.get("skipped", 0))
                except Exception as xml_error:
                    # Fallback to simple stats if XML parsing fails
                    result["error"] = f"Error parsing XML report: {str(xml_error)}"
                    
                    # Use regex to extract test summary from stdout
                    summary_regex = r"=+ (.+) in (.+) seconds =+"
                    summary_match = re.search(summary_regex, stdout)
                    if summary_match:
                        summary_text = summary_match.group(1).strip()
                        # Parse summary text like "5 passed, 2 failed, 1 skipped"
                        passed_match = re.search(r"(\d+) passed", summary_text)
                        failed_match = re.search(r"(\d+) failed", summary_text)
                        skipped_match = re.search(r"(\d+) skipped", summary_text)
                        error_match = re.search(r"(\d+) error", summary_text)
                        xfailed_match = re.search(r"(\d+) xfailed", summary_text)
                        xpassed_match = re.search(r"(\d+) xpassed", summary_text)
                        
                        result["summary"]["passed"] = int(passed_match.group(1)) if passed_match else 0
                        result["summary"]["failed"] = int(failed_match.group(1)) if failed_match else 0
                        result["summary"]["skipped"] = int(skipped_match.group(1)) if skipped_match else 0
                        result["summary"]["error"] = int(error_match.group(1)) if error_match else 0
                        result["summary"]["xfailed"] = int(xfailed_match.group(1)) if xfailed_match else 0
                        result["summary"]["xpassed"] = int(xpassed_match.group(1)) if xpassed_match else 0
                        result["summary"]["total"] = (
                            result["summary"]["passed"] + 
                            result["summary"]["failed"] + 
                            result["summary"]["skipped"] + 
                            result["summary"]["error"] + 
                            result["summary"]["xfailed"] + 
                            result["summary"]["xpassed"]
                        )
            
            # Copy JUnit XML to specified location if requested
            if junit_xml:
                os.makedirs(os.path.dirname(os.path.abspath(junit_xml)), exist_ok=True)
                if os.path.exists(xml_report_path):
                    import shutil
                    shutil.copy(xml_report_path, junit_xml)
    
    except Exception as e:
        result["error"] = f"Error running tests: {str(e)}"
    
    return result

@ray.remote
def aggregate_test_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Aggregate test results from multiple files
    
    Args:
        results: List of test results from individual files
        
    Returns:
        Dictionary with aggregated test results
    """
    aggregated = {
        "summary": {
            "total_files": len(results),
            "files_with_errors": 0,
            "total_tests": 0,
            "passed_tests": 0,
            "failed_tests": 0,
            "skipped_tests": 0,
            "error_tests": 0,
            "xfailed_tests": 0,
            "xpassed_tests": 0,
            "execution_time": 0
        },
        "file_results": [],
        "all_tests": [],
        "failed_tests": [],
        "error_files": []
    }
    
    # Process each file's results
    for file_result in results:
        # Extract file summary
        file_summary = file_result.get("summary", {})
        file_path = file_result.get("file_path", "unknown")
        
        # Update execution time
        aggregated["summary"]["execution_time"] += file_result.get("duration", 0)
        
        # Check for file-level errors
        if file_result.get("error"):
            aggregated["summary"]["files_with_errors"] += 1
            aggregated["error_files"].append({
                "file_path": file_path,
                "error": file_result["error"]
            })
        
        # Add file results to aggregated results
        aggregated["file_results"].append({
            "file_path": file_path,
            "summary": file_summary,
            "duration": file_result.get("duration", 0),
            "error": file_result.get("error")
        })
        
        # Update test counts
        aggregated["summary"]["total_tests"] += file_summary.get("total", 0)
        aggregated["summary"]["passed_tests"] += file_summary.get("passed", 0)
        aggregated["summary"]["failed_tests"] += file_summary.get("failed", 0)
        aggregated["summary"]["skipped_tests"] += file_summary.get("skipped", 0)
        aggregated["summary"]["error_tests"] += file_summary.get("error", 0)
        aggregated["summary"]["xfailed_tests"] += file_summary.get("xfailed", 0)
        aggregated["summary"]["xpassed_tests"] += file_summary.get("xpassed", 0)
        
        # Add tests to all_tests list
        for test in file_result.get("tests", []):
            # Add test to all tests
            aggregated["all_tests"].append(test)
            
            # Add failed tests to failed_tests list
            if test.get("status") in ["failed", "error"]:
                aggregated["failed_tests"].append(test)
    
    # Sort failed tests by file and name
    aggregated["failed_tests"].sort(key=lambda x: (x.get("file", ""), x.get("classname", ""), x.get("name", "")))
    
    # Calculate pass rate
    total_run = (
        aggregated["summary"]["passed_tests"] + 
        aggregated["summary"]["failed_tests"] + 
        aggregated["summary"]["error_tests"]
    )
    aggregated["summary"]["pass_rate"] = (
        aggregated["summary"]["passed_tests"] / total_run if total_run > 0 else 0
    )
    
    return aggregated

def run_tests(
    directory: str,
    test_pattern: str = "test_*.py",
    exclude_dirs: Optional[List[str]] = None,
    pytest_args: Optional[List[str]] = None,
    verbose: bool = False,
    batch_size: int = 5,
    junit_xml_dir: Optional[str] = None,
    collect_coverage: bool = False,
    output_file: Optional[str] = None
) -> Dict[str, Any]:
    """
    Run tests in parallel using Ray
    
    Args:
        directory: Directory containing test files
        test_pattern: Pattern to match test files
        exclude_dirs: Directories to exclude
        pytest_args: Additional arguments for pytest
        verbose: Whether to run pytest in verbose mode
        batch_size: Number of test files to process in each batch
        junit_xml_dir: Directory to store JUnit XML reports
        collect_coverage: Whether to collect coverage data
        output_file: File to write results to
        
    Returns:
        Dictionary with test results
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
    
    # Find test files
    logger.info(f"Finding test files in {directory}...")
    test_files = find_test_files(directory, test_pattern, exclude_dirs)
    
    if not test_files:
        logger.warning(f"No test files found in {directory} matching pattern {test_pattern}")
        return {
            "summary": {
                "total_files": 0,
                "total_tests": 0,
                "execution_time": 0
            },
            "error": "No test files found"
        }
    
    logger.info(f"Found {len(test_files)} test files to process")
    
    # Function to track progress
    def show_progress(current, total):
        logger.info(f"Progress: {current}/{total} files ({current/total*100:.1f}%)")
    
    # Prepare JUnit XML paths if needed
    junit_xml_paths = None
    if junit_xml_dir:
        os.makedirs(junit_xml_dir, exist_ok=True)
        junit_xml_paths = [
            os.path.join(junit_xml_dir, f"{os.path.basename(f)}.xml")
            for f in test_files
        ]
    
    # Use task manager to distribute test tasks
    remote_args_list = []
    for i, file_path in enumerate(test_files):
        args = {
            "pytest_args": pytest_args,
            "verbose": verbose,
            "coverage": collect_coverage
        }
        
        # Add JUnit XML path if needed
        if junit_xml_paths:
            args["junit_xml"] = junit_xml_paths[i]
        
        remote_args_list.append(args)
    
    # Distribute tasks
    results = distribute_tasks(
        task_func=run_test_file,
        items=test_files,
        task_type="testing",
        batch_size=batch_size,
        progress_callback=show_progress,
        retry_attempts=1,  # Don't retry test failures
        remote_args_list=remote_args_list
    )
    
    # Aggregate results
    logger.info("Aggregating test results...")
    aggregated = ray.get(aggregate_test_results.remote(results))
    
    # Add total execution time
    total_time = time.time() - start_time
    aggregated["total_execution_time"] = total_time
    
    # Write results to file if specified
    if output_file:
        with open(output_file, 'w') as f:
            json.dump(aggregated, f, indent=2)
        logger.info(f"Results written to {output_file}")
    
    return aggregated

def print_summary(results: Dict[str, Any], verbose: bool = False) -> None:
    """
    Print a summary of the test results
    
    Args:
        results: Dictionary with test results
        verbose: Whether to print verbose output
    """
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    
    summary = results.get("summary", {})
    total_files = summary.get("total_files", 0)
    total_tests = summary.get("total_tests", 0)
    
    print(f"Files processed:  {total_files}")
    if summary.get("files_with_errors", 0) > 0:
        print(f"Files with errors: {summary.get('files_with_errors', 0)}")
    
    print(f"Total tests:      {total_tests}")
    print(f"Passed tests:     {summary.get('passed_tests', 0)}")
    print(f"Failed tests:     {summary.get('failed_tests', 0)}")
    
    if summary.get("error_tests", 0) > 0:
        print(f"Tests with errors: {summary.get('error_tests', 0)}")
    
    if summary.get("skipped_tests", 0) > 0:
        print(f"Skipped tests:   {summary.get('skipped_tests', 0)}")
    
    if summary.get("xfailed_tests", 0) > 0:
        print(f"Expected failures: {summary.get('xfailed_tests', 0)}")
    
    if summary.get("xpassed_tests", 0) > 0:
        print(f"Unexpected passes: {summary.get('xpassed_tests', 0)}")
    
    # Print pass rate
    pass_rate = summary.get("pass_rate", 0) * 100
    print(f"Pass rate:        {pass_rate:.1f}%")
    
    # Show execution time
    execution_time = results.get("total_execution_time", 0)
    print(f"Execution time:   {execution_time:.2f} seconds")
    
    # Print file errors if any
    error_files = results.get("error_files", [])
    if error_files:
        print("\nFiles with errors:")
        for file_error in error_files:
            print(f"  {file_error['file_path']}: {file_error['error']}")
    
    # Print failed tests
    failed_tests = results.get("failed_tests", [])
    if failed_tests:
        print("\nFailed tests:")
        for i, test in enumerate(failed_tests):
            classname = test.get("classname", "")
            name = test.get("name", "")
            print(f"  {i+1}. {classname}::{name}")
            
            # Print failure message in verbose mode
            if verbose and test.get("message"):
                message = test.get("message", "")
                print(f"     {message}")
                
                # Print detail if available and not too long
                detail = test.get("detail", "")
                if detail and len(detail) < 1000:  # Limit to keep output manageable
                    # Get the first few lines
                    detail_lines = detail.split("\n")[:5]
                    print("     " + "\n     ".join(detail_lines))
                    if len(detail_lines) < len(detail.split("\n")):
                        print("     ...")
    
    # Print result indicator
    print("\n" + "="*60)
    if summary.get("failed_tests", 0) > 0 or summary.get("error_tests", 0) > 0:
        print("TEST RUN FAILED")
    else:
        print("TEST RUN PASSED")
    print("="*60)

def main():
    parser = argparse.ArgumentParser(description="Run tests in parallel using Ray")
    parser.add_argument("--dir", "-d", type=str, default=".", help="Directory containing test files (default: current directory)")
    parser.add_argument("--pattern", "-p", type=str, default="test_*.py", help="Pattern to match test files (default: test_*.py)")
    parser.add_argument("--exclude", "-e", type=str, nargs="+", help="Directories to exclude")
    parser.add_argument("--batch-size", "-b", type=int, default=5, help="Batch size for processing (default: 5)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Run pytest in verbose mode and show detailed output")
    parser.add_argument("--output-file", "-o", type=str, help="File to write results to")
    parser.add_argument("--junit-xml-dir", type=str, help="Directory to store JUnit XML reports")
    parser.add_argument("--coverage", "-c", action="store_true", help="Collect coverage data")
    parser.add_argument("--pytest-args", type=str, nargs="+", help="Additional arguments to pass to pytest")
    
    args = parser.parse_args()
    
    # Run tests
    try:
        logger.info("Starting test run...")
        results = run_tests(
            directory=args.dir,
            test_pattern=args.pattern,
            exclude_dirs=args.exclude,
            pytest_args=args.pytest_args,
            verbose=args.verbose,
            batch_size=args.batch_size,
            junit_xml_dir=args.junit_xml_dir,
            collect_coverage=args.coverage,
            output_file=args.output_file
        )
        
        # Print summary
        print_summary(results, verbose=args.verbose)
        
        # Determine exit code based on test results
        has_failures = (
            results.get("summary", {}).get("failed_tests", 0) > 0 or
            results.get("summary", {}).get("error_tests", 0) > 0
        )
        exit_code = 1 if has_failures else 0
        
        # Shutdown Ray
        ray.shutdown()
        
        # Exit with appropriate code
        sys.exit(exit_code)
        
    except Exception as e:
        logger.error(f"Error running tests: {str(e)}")
        ray.shutdown()
        sys.exit(1)

if __name__ == "__main__":
    main()
