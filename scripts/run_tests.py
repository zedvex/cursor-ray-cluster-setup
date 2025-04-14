#!/usr/bin/env python3
"""
Script for running tests in parallel using Ray cluster.
Distributes test execution across the cluster and aggregates results.
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from collections import defaultdict
from glob import glob
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Any, Union

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
    parser = argparse.ArgumentParser(description="Run tests in parallel using Ray")
    parser.add_argument(
        "--ray-address",
        type=str,
        default="auto",
        help="Ray cluster address (default: auto)",
    )
    parser.add_argument(
        "--directory",
        type=str,
        default="tests",
        help="Directory containing tests (default: tests)",
    )
    parser.add_argument(
        "--test-pattern",
        type=str,
        default="test_*.py",
        help="Pattern for test files (default: test_*.py)",
    )
    parser.add_argument(
        "--exclude",
        type=str,
        default="",
        help="Comma-separated list of test files or patterns to exclude",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="count",
        default=0,
        help="Set test verbosity level (can be used multiple times, e.g. -vv)",
    )
    parser.add_argument(
        "--junit-xml",
        type=str,
        help="Output JUnit XML file for test results",
    )
    parser.add_argument(
        "--coverage",
        action="store_true",
        help="Run tests with coverage",
    )
    parser.add_argument(
        "--coverage-report",
        type=str,
        default="coverage_report",
        help="Directory to store coverage reports (default: coverage_report)",
    )
    parser.add_argument(
        "--pytest-args",
        type=str,
        default="",
        help="Additional pytest arguments (quoted)",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output file for test results (default: stdout)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    return parser.parse_args()

def find_test_files(directory: str, pattern: str, exclude_patterns: List[str]) -> List[str]:
    """Find all test files in a directory, excluding specified patterns."""
    all_test_files = glob(os.path.join(directory, "**", pattern), recursive=True)
    
    # Filter out excluded files
    if exclude_patterns:
        filtered_test_files = []
        for file_path in all_test_files:
            if not any(
                exclude_pattern in file_path
                for exclude_pattern in exclude_patterns
            ):
                filtered_test_files.append(file_path)
        test_files = filtered_test_files
    else:
        test_files = all_test_files
    
    logger.info(f"Found {len(test_files)} test files to run")
    return test_files

@ray.remote
def run_test_file(
    file_path: str, 
    verbose: int, 
    junit_xml: Optional[str] = None,
    coverage: bool = False,
    coverage_report: Optional[str] = None,
    pytest_args: str = ""
) -> Dict[str, Any]:
    """Run tests from a file using pytest."""
    start_time = time.time()
    result = {
        "file": file_path,
        "success": False,
        "output": "",
        "error": "",
        "duration": 0,
        "test_count": 0,
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "xfailed": 0,
        "xpassed": 0,
    }
    
    # Prepare pytest command
    cmd = ["python", "-m", "pytest"]
    
    # Add verbosity flags
    for _ in range(verbose):
        cmd.append("-v")
    
    # Add JUnit XML flag if specified
    if junit_xml:
        file_specific_junit = f"{os.path.splitext(junit_xml)[0]}_{os.path.basename(file_path)}.xml"
        cmd.extend(["--junitxml", file_specific_junit])
    
    # Add coverage flags if specified
    if coverage:
        cmd.extend(["--cov", project_root])
        if coverage_report:
            os.makedirs(coverage_report, exist_ok=True)
            file_specific_report = os.path.join(
                coverage_report, 
                f"coverage_{os.path.basename(file_path)}"
            )
            cmd.extend(["--cov-report", f"html:{file_specific_report}"])
    
    # Add pytest arguments if specified
    if pytest_args:
        cmd.extend(pytest_args.split())
    
    # Add the test file
    cmd.append(file_path)
    
    try:
        # Run pytest
        process = subprocess.run(
            cmd, 
            capture_output=True, 
            text=True,
            check=False
        )
        
        result["output"] = process.stdout
        result["error"] = process.stderr
        result["success"] = process.returncode == 0
        
        # Parse test summary
        try:
            if "collected" in process.stdout:
                summary_line = [
                    line for line in process.stdout.splitlines()
                    if "passed" in line or "failed" in line or "error" in line
                ]
                if summary_line:
                    summary = summary_line[-1]
                    # Extract test counts
                    parts = summary.strip().split()
                    for part in parts:
                        if "passed" in part:
                            result["passed"] = int(part.split()[0])
                        elif "failed" in part:
                            result["failed"] = int(part.split()[0])
                        elif "skipped" in part:
                            result["skipped"] = int(part.split()[0])
                        elif "xfailed" in part:
                            result["xfailed"] = int(part.split()[0])
                        elif "xpassed" in part:
                            result["xpassed"] = int(part.split()[0])
                    
                    result["test_count"] = (
                        result["passed"] + result["failed"] + 
                        result["skipped"] + result["xfailed"] + 
                        result["xpassed"]
                    )
        except Exception as e:
            result["error"] += f"\nError parsing test summary: {str(e)}"
        
    except Exception as e:
        result["error"] = str(e)
    
    # Record duration
    result["duration"] = time.time() - start_time
    
    return result

def aggregate_test_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate test results from multiple files."""
    aggregate = {
        "total_files": len(results),
        "successful_files": 0,
        "failed_files": 0,
        "total_tests": 0,
        "total_passed": 0,
        "total_failed": 0,
        "total_skipped": 0,
        "total_xfailed": 0,
        "total_xpassed": 0,
        "total_duration": 0,
        "file_results": results,
    }
    
    for result in results:
        if result["success"]:
            aggregate["successful_files"] += 1
        else:
            aggregate["failed_files"] += 1
        
        aggregate["total_tests"] += result["test_count"]
        aggregate["total_passed"] += result["passed"]
        aggregate["total_failed"] += result["failed"]
        aggregate["total_skipped"] += result["skipped"]
        aggregate["total_xfailed"] += result["xfailed"]
        aggregate["total_xpassed"] += result["xpassed"]
        aggregate["total_duration"] += result["duration"]
    
    return aggregate

def write_results_to_file(results: Dict[str, Any], output_file: str) -> None:
    """Write test results to a JSON file."""
    try:
        with open(output_file, "w") as f:
            json.dump(results, f, indent=2)
        logger.info(f"Test results written to {output_file}")
    except Exception as e:
        logger.error(f"Failed to write results to {output_file}: {e}")

def generate_junit_xml(results: Dict[str, Any], junit_xml: str) -> None:
    """Generate a combined JUnit XML file from individual test runs."""
    import xml.etree.ElementTree as ET
    from xml.etree.ElementTree import Element
    
    # Create the root element
    root = Element("testsuites")
    
    # Combine individual JUnit XML files
    for result in results["file_results"]:
        file_name = result["file"]
        file_specific_junit = f"{os.path.splitext(junit_xml)[0]}_{os.path.basename(file_name)}.xml"
        
        try:
            if os.path.exists(file_specific_junit):
                tree = ET.parse(file_specific_junit)
                testsuite = tree.getroot()
                root.append(testsuite)
                
                # Clean up individual JUnit XML file
                os.remove(file_specific_junit)
        except Exception as e:
            logger.error(f"Error processing JUnit XML for {file_name}: {e}")
    
    # Write the combined JUnit XML file
    try:
        tree = ET.ElementTree(root)
        tree.write(junit_xml, encoding="utf-8", xml_declaration=True)
        logger.info(f"Combined JUnit XML written to {junit_xml}")
    except Exception as e:
        logger.error(f"Failed to write combined JUnit XML: {e}")

def generate_coverage_report(coverage_report: str) -> None:
    """Generate a combined coverage report."""
    try:
        cmd = [
            "python", "-m", "coverage", "combine", 
            "--append", ".coverage.*"
        ]
        subprocess.run(cmd, check=False)
        
        cmd = [
            "python", "-m", "coverage", "html", 
            "-d", coverage_report
        ]
        subprocess.run(cmd, check=False)
        
        cmd = [
            "python", "-m", "coverage", "report"
        ]
        report_process = subprocess.run(
            cmd, 
            capture_output=True, 
            text=True,
            check=False
        )
        
        logger.info(f"Coverage report:\n{report_process.stdout}")
        logger.info(f"HTML coverage report written to {coverage_report}")
    except Exception as e:
        logger.error(f"Failed to generate coverage report: {e}")

def print_summary(results: Dict[str, Any]) -> None:
    """Print a summary of the test results."""
    logger.info("\nTest Summary:")
    logger.info("-------------")
    logger.info(f"Total test files: {results['total_files']}")
    logger.info(f"Successful test files: {results['successful_files']}")
    logger.info(f"Failed test files: {results['failed_files']}")
    logger.info(f"Total tests: {results['total_tests']}")
    logger.info(f"Tests passed: {results['total_passed']}")
    logger.info(f"Tests failed: {results['total_failed']}")
    logger.info(f"Tests skipped: {results['total_skipped']}")
    logger.info(f"Tests xfailed: {results['total_xfailed']}")
    logger.info(f"Tests xpassed: {results['total_xpassed']}")
    logger.info(f"Total duration: {results['total_duration']:.2f} seconds")
    
    # Print files with failures if any
    if results['failed_files'] > 0:
        logger.info("\nFiles with test failures:")
        for file_result in results['file_results']:
            if not file_result['success']:
                logger.info(f"  {file_result['file']} - {file_result['failed']} failed tests")
                # Print the first few lines of the error for context
                if file_result['error']:
                    error_lines = file_result['error'].split('\n')
                    error_preview = '\n    '.join(error_lines[:5])
                    if len(error_lines) > 5:
                        error_preview += "\n    ..."
                    logger.info(f"    Error: {error_preview}")

def main() -> int:
    """Main entry point for the test runner script."""
    args = parse_args()
    
    if args.debug:
        logger.setLevel(logging.DEBUG)
    
    # Convert directory to absolute path
    directory = os.path.abspath(args.directory)
    
    if not os.path.isdir(directory):
        logger.error(f"Directory {directory} does not exist or is not a directory")
        return 1
    
    # Parse exclude patterns
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
        # Find test files
        test_files = find_test_files(directory, args.test_pattern, exclude_patterns)
        
        if not test_files:
            logger.info("No test files found. Exiting.")
            return 0
        
        # Run tests in parallel
        test_tasks = [
            run_test_file.remote(
                file_path, 
                args.verbose,
                args.junit_xml,
                args.coverage,
                args.coverage_report,
                args.pytest_args
            )
            for file_path in test_files
        ]
        
        # Get results
        test_results = ray.get(test_tasks)
        
        # Aggregate results
        aggregated_results = aggregate_test_results(test_results)
        
        # Print summary
        print_summary(aggregated_results)
        
        # Generate combined JUnit XML if specified
        if args.junit_xml:
            generate_junit_xml(aggregated_results, args.junit_xml)
        
        # Generate combined coverage report if specified
        if args.coverage and args.coverage_report:
            generate_coverage_report(args.coverage_report)
        
        # Write results to file if specified
        if args.output:
            write_results_to_file(aggregated_results, args.output)
        
        # Return appropriate exit code
        if aggregated_results["failed_files"] > 0:
            logger.info("Some tests failed")
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
