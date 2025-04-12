import ray
import os
import subprocess
from typing import List, Dict, Tuple, Union
import logging
from .task_manager import retry_task

logger = logging.getLogger(__name__)

@retry_task(max_attempts=3)
@ray.remote
def lint_file(filepath: str) -> Tuple[str, Dict[str, int]]:
    """
    Lint a Python file using multiple linters and return metrics
    
    Args:
        filepath: Path to the file to lint
        
    Returns:
        Tuple of (filepath, metrics_dict)
    """
    metrics = {
        "loc": 0,  # Lines of code
        "functions": 0,  # Number of functions
        "classes": 0,  # Number of classes
        "flake8_errors": 0,  # Flake8 errors
        "mypy_errors": 0,  # MyPy errors
    }
    
    # Count lines, functions, and classes
    try:
        with open(filepath, 'r') as f:
            content = f.read()
            lines = content.split('\n')
            metrics["loc"] = len(lines)
            metrics["functions"] = content.count("def ")
            metrics["classes"] = content.count("class ")
    except Exception as e:
        logger.error(f"Error analyzing {filepath}: {e}")
    
    # Run flake8 if available
    try:
        result = subprocess.run(
            ["flake8", filepath], 
            capture_output=True, 
            text=True
        )
        metrics["flake8_errors"] = len(result.stdout.strip().split('\n')) if result.stdout.strip() else 0
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        logger.warning(f"Flake8 analysis failed for {filepath}: {e}")
    
    # Run mypy if available
    try:
        result = subprocess.run(
            ["mypy", filepath], 
            capture_output=True, 
            text=True
        )
        metrics["mypy_errors"] = len(result.stdout.strip().split('\n')) if result.stdout.strip() else 0
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        logger.warning(f"MyPy analysis failed for {filepath}: {e}")
    
    return filepath, metrics

@ray.remote
def aggregate_lint_results(results: List[Tuple[str, Dict[str, int]]]) -> Dict[str, Union[int, Dict[str, int]]]:
    """
    Aggregate linting results from multiple files
    
    Args:
        results: List of (filepath, metrics) tuples
        
    Returns:
        Dictionary with aggregated metrics
    """
    aggregated = {
        "total_files": len(results),
        "total_loc": 0,
        "total_functions": 0,
        "total_classes": 0,
        "total_flake8_errors": 0,
        "total_mypy_errors": 0,
        "files_with_errors": 0,
        "error_density": {},  # Errors per line of code by file
    }
    
    for filepath, metrics in results:
        aggregated["total_loc"] += metrics["loc"]
        aggregated["total_functions"] += metrics["functions"]
        aggregated["total_classes"] += metrics["classes"]
        aggregated["total_flake8_errors"] += metrics["flake8_errors"]
        aggregated["total_mypy_errors"] += metrics["mypy_errors"]
        
        if metrics["flake8_errors"] > 0 or metrics["mypy_errors"] > 0:
            aggregated["files_with_errors"] += 1
            
        # Calculate error density
        if metrics["loc"] > 0:
            total_errors = metrics["flake8_errors"] + metrics["mypy_errors"]
            density = total_errors / metrics["loc"]
            file_id = os.path.basename(filepath)
            aggregated["error_density"][file_id] = density
    
    return aggregated