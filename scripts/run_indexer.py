#!/usr/bin/env python3
"""
Script to run code indexing in parallel using the Ray cluster.
Indexes Python code for building a knowledge base for code navigation,
documentation generation, and other code intelligence features.
"""

import os
import sys
import time
import argparse
import logging
import json
from typing import List, Dict, Any, Optional, Set, Tuple
import ast
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
def index_file(
    file_path: str,
    include_docstrings: bool = True,
    include_imports: bool = True,
    include_functions: bool = True,
    include_classes: bool = True,
    include_variables: bool = True,
    include_line_numbers: bool = True
) -> Dict[str, Any]:
    """
    Index a single Python file
    
    Args:
        file_path: Path to the file to index
        include_docstrings: Whether to include docstrings in the index
        include_imports: Whether to include imports in the index
        include_functions: Whether to include functions in the index
        include_classes: Whether to include classes in the index
        include_variables: Whether to include module-level variables in the index
        include_line_numbers: Whether to include line numbers in the index
        
    Returns:
        Dictionary with indexing results
    """
    result = {
        "file_path": file_path,
        "module_name": os.path.splitext(os.path.basename(file_path))[0],
        "imports": [],
        "functions": [],
        "classes": [],
        "variables": [],
        "docstring": None,
        "error": None
    }
    
    # Check if file exists
    if not os.path.exists(file_path):
        result["error"] = f"File not found: {file_path}"
        return result
    
    try:
        # Read the file
        with open(file_path, 'r', encoding='utf-8') as f:
            source_code = f.read()
        
        # Parse the AST
        tree = ast.parse(source_code, filename=file_path)
        
        # Extract module docstring if it exists
        if include_docstrings and ast.get_docstring(tree):
            result["docstring"] = ast.get_docstring(tree)
        
        # Create visitor to extract information
        class CodeIndexVisitor(ast.NodeVisitor):
            def __init__(self):
                self.imports = []
                self.functions = []
                self.classes = []
                self.variables = []
            
            def visit_Import(self, node):
                if include_imports:
                    for name in node.names:
                        self.imports.append({
                            "name": name.name,
                            "alias": name.asname,
                            "line": node.lineno if include_line_numbers else None
                        })
                self.generic_visit(node)
            
            def visit_ImportFrom(self, node):
                if include_imports:
                    module = node.module
                    for name in node.names:
                        self.imports.append({
                            "name": f"{module}.{name.name}" if module else name.name,
                            "alias": name.asname,
                            "line": node.lineno if include_line_numbers else None
                        })
                self.generic_visit(node)
            
            def visit_FunctionDef(self, node):
                if include_functions:
                    params = []
                    for arg in node.args.args:
                        params.append(arg.arg)
                    
                    self.functions.append({
                        "name": node.name,
                        "params": params,
                        "docstring": ast.get_docstring(node) if include_docstrings else None,
                        "line": node.lineno if include_line_numbers else None,
                        "end_line": node.end_lineno if include_line_numbers and hasattr(node, 'end_lineno') else None,
                        "decorator_list": [d.id if isinstance(d, ast.Name) else None for d in node.decorator_list],
                        "is_async": isinstance(node, ast.AsyncFunctionDef)
                    })
                self.generic_visit(node)
            
            def visit_AsyncFunctionDef(self, node):
                self.visit_FunctionDef(node)  # Reuse the same logic
            
            def visit_ClassDef(self, node):
                if include_classes:
                    bases = []
                    for base in node.bases:
                        if isinstance(base, ast.Name):
                            bases.append(base.id)
                        elif isinstance(base, ast.Attribute):
                            bases.append(f"{base.value.id}.{base.attr}" if isinstance(base.value, ast.Name) else "...")
                    
                    methods = []
                    class_vars = []
                    
                    # Find methods and class variables
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            params = []
                            for arg in item.args.args:
                                params.append(arg.arg)
                            
                            methods.append({
                                "name": item.name,
                                "params": params,
                                "docstring": ast.get_docstring(item) if include_docstrings else None,
                                "line": item.lineno if include_line_numbers else None,
                                "is_async": isinstance(item, ast.AsyncFunctionDef)
                            })
                        elif isinstance(item, ast.Assign) and include_variables:
                            for target in item.targets:
                                if isinstance(target, ast.Name):
                                    class_vars.append({
                                        "name": target.id,
                                        "line": item.lineno if include_line_numbers else None
                                    })
                    
                    self.classes.append({
                        "name": node.name,
                        "bases": bases,
                        "docstring": ast.get_docstring(node) if include_docstrings else None,
                        "methods": methods,
                        "class_vars": class_vars,
                        "line": node.lineno if include_line_numbers else None,
                        "end_line": node.end_lineno if include_line_numbers and hasattr(node, 'end_lineno') else None,
                        "decorator_list": [d.id if isinstance(d, ast.Name) else None for d in node.decorator_list]
                    })
                self.generic_visit(node)
            
            def visit_Assign(self, node):
                if include_variables:
                    # Only include module-level variables
                    if isinstance(node.parent, ast.Module):
                        for target in node.targets:
                            if isinstance(target, ast.Name):
                                self.variables.append({
                                    "name": target.id,
                                    "line": node.lineno if include_line_numbers else None,
                                    "value_type": type(node.value).__name__
                                })
                self.generic_visit(node)
        
        # Add parent references to AST for better context
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                child.parent = node
        
        # Visit the AST
        visitor = CodeIndexVisitor()
        visitor.visit(tree)
        
        # Add the extracted information to the result
        result["imports"] = visitor.imports
        result["functions"] = visitor.functions
        result["classes"] = visitor.classes
        result["variables"] = visitor.variables
        
    except Exception as e:
        result["error"] = f"Error parsing {file_path}: {str(e)}"
    
    return result

@ray.remote
def aggregate_index_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Aggregate indexing results from multiple files
    
    Args:
        results: List of indexing results
        
    Returns:
        Dictionary with aggregated indexing results
    """
    # Total counts
    total_files = len(results)
    successful_files = sum(1 for r in results if r.get("error") is None)
    failed_files = total_files - successful_files
    
    # Error files (files that couldn't be indexed)
    error_files = [
        {"file_path": r["file_path"], "error": r["error"]}
        for r in results
        if r.get("error") is not None
    ]
    
    # Collect all unique imports across all files
    all_imports = {}
    
    # Collect class and function definitions for cross-referencing
    all_definitions = {
        "classes": {},
        "functions": {}
    }
    
    # Import dependencies between files
    import_graph = {}
    
    # Process each file's results
    for r in results:
        if r.get("error") is not None:
            continue
        
        file_path = r["file_path"]
        module_name = r["module_name"]
        
        # Track all imports and where they're used
        for imp in r.get("imports", []):
            import_name = imp["name"]
            if import_name not in all_imports:
                all_imports[import_name] = {"used_in": set()}
            all_imports[import_name]["used_in"].add(file_path)
        
        # Track all class definitions
        for cls in r.get("classes", []):
            class_name = cls["name"]
            full_name = f"{module_name}.{class_name}"
            all_definitions["classes"][full_name] = {
                "defined_in": file_path,
                "docstring": cls.get("docstring"),
                "bases": cls.get("bases", []),
                "method_count": len(cls.get("methods", [])),
                "class_var_count": len(cls.get("class_vars", [])),
                "line": cls.get("line")
            }
        
        # Track all function definitions
        for func in r.get("functions", []):
            func_name = func["name"]
            full_name = f"{module_name}.{func_name}"
            all_definitions["functions"][full_name] = {
                "defined_in": file_path,
                "docstring": func.get("docstring"),
                "param_count": len(func.get("params", [])),
                "is_async": func.get("is_async", False),
                "line": func.get("line")
            }
        
        # Create import dependency graph
        import_graph[file_path] = {
            "imports": [imp["name"] for imp in r.get("imports", [])]
        }
    
    # Convert sets to lists for JSON serialization
    for imp_name, imp_data in all_imports.items():
        imp_data["used_in"] = list(imp_data["used_in"])
    
    # Create overall summary
    summary = {
        "total_files": total_files,
        "successful_files": successful_files,
        "failed_files": failed_files,
        "error_files": error_files,
        "total_imports": len(all_imports),
        "total_classes": len(all_definitions["classes"]),
        "total_functions": len(all_definitions["functions"]),
        "imports": all_imports,
        "definitions": all_definitions,
        "import_graph": import_graph
    }
    
    return summary

def create_index(
    directory: str,
    batch_size: int = 10,
    exclude_dirs: Optional[List[str]] = None,
    output_file: Optional[str] = None,
    include_docstrings: bool = True,
    include_imports: bool = True,
    include_functions: bool = True,
    include_classes: bool = True,
    include_variables: bool = True,
    include_line_numbers: bool = True
) -> Dict[str, Any]:
    """
    Index all Python files in the specified directory using Ray
    
    Args:
        directory: Directory containing Python files to index
        batch_size: Number of files to process in each batch
        exclude_dirs: Directories to exclude
        output_file: File to write results to
        include_docstrings: Whether to include docstrings in the index
        include_imports: Whether to include imports in the index
        include_functions: Whether to include functions in the index
        include_classes: Whether to include classes in the index
        include_variables: Whether to include variables in the index
        include_line_numbers: Whether to include line numbers in the index
        
    Returns:
        Dictionary with indexing results
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
    
    logger.info(f"Found {len(python_files)} Python files to index")
    
    # Function to track progress
    def show_progress(current, total):
        logger.info(f"Progress: {current}/{total} files ({current/total*100:.1f}%)")
    
    # Use task manager to distribute indexing tasks
    results = distribute_tasks(
        task_func=index_file,
        items=python_files,
        task_type="indexing",
        batch_size=batch_size,
        progress_callback=show_progress,
        retry_attempts=2,
        # Pass indexing parameters
        remote_args={
            "include_docstrings": include_docstrings,
            "include_imports": include_imports,
            "include_functions": include_functions,
            "include_classes": include_classes,
            "include_variables": include_variables,
            "include_line_numbers": include_line_numbers
        }
    )
    
    # Aggregate results
    logger.info("Aggregating indexing results...")
    aggregated = ray.get(aggregate_index_results.remote(results))
    
    # Add execution time
    elapsed_time = time.time() - start_time
    aggregated["execution_time"] = elapsed_time
    
    # Write results to file if specified
    if output_file:
        with open(output_file, 'w') as f:
            json.dump(aggregated, f, indent=2)
        logger.info(f"Index written to {output_file}")
    
    return aggregated

def print_summary(results: Dict[str, Any]) -> None:
    """
    Print a summary of the indexing results
    
    Args:
        results: Dictionary with indexing results
    """
    print("\n" + "="*60)
    print("CODE INDEX SUMMARY")
    print("="*60)
    
    print(f"Total files processed: {results['total_files']}")
    print(f"Successfully indexed: {results['successful_files']}")
    print(f"Failed to index: {results['failed_files']}")
    
    print("\nIndexed items:")
    print(f"  Imports:   {results['total_imports']}")
    print(f"  Classes:   {results['total_classes']}")
    print(f"  Functions: {results['total_functions']}")
    
    # Print errors if any
    if results.get("error_files", []):
        print("\nFiles with errors:")
        for error_file in results["error_files"]:
            print(f"  {error_file['file_path']}: {error_file['error']}")
    
    # Print most common imports
    import_counts = {
        import_name: len(data.get("used_in", []))
        for import_name, data in results.get("imports", {}).items()
    }
    
    if import_counts:
        print("\nMost common imports:")
        # Sort imports by usage count (descending)
        sorted_imports = sorted(import_counts.items(), key=lambda x: x[1], reverse=True)
        for imp, count in sorted_imports[:10]:  # Show top 10
            print(f"  {imp}: used in {count} files")
    
    # Print execution time
    if "execution_time" in results:
        print(f"\nExecution time: {results['execution_time']:.2f} seconds")
    
    print("="*60)

def main():
    parser = argparse.ArgumentParser(description="Index Python code using Ray")
    parser.add_argument("--dir", "-d", type=str, default=".", help="Directory to index (default: current directory)")
    parser.add_argument("--batch-size", "-b", type=int, default=10, help="Batch size for processing (default: 10)")
    parser.add_argument("--exclude", "-e", type=str, nargs="+", help="Directories to exclude")
    parser.add_argument("--output-file", "-o", type=str, help="File to write index to")
    
    # Index content options
    parser.add_argument("--no-docstrings", action="store_true", help="Don't include docstrings in the index")
    parser.add_argument("--no-imports", action="store_true", help="Don't include imports in the index")
    parser.add_argument("--no-functions", action="store_true", help="Don't include functions in the index")
    parser.add_argument("--no-classes", action="store_true", help="Don't include classes in the index")
    parser.add_argument("--no-variables", action="store_true", help="Don't include variables in the index")
    parser.add_argument("--no-line-numbers", action="store_true", help="Don't include line numbers in the index")
    
    args = parser.parse_args()
    
    # Run indexer
    try:
        logger.info("Starting code indexing run...")
        results = create_index(
            directory=args.dir,
            batch_size=args.batch_size,
            exclude_dirs=args.exclude,
            output_file=args.output_file,
            include_docstrings=not args.no_docstrings,
            include_imports=not args.no_imports,
            include_functions=not args.no_functions,
            include_classes=not args.no_classes,
            include_variables=not args.no_variables,
            include_line_numbers=not args.no_line_numbers
        )
        
        # Print summary
        print_summary(results)
        
        # Shutdown Ray
        ray.shutdown()
        
    except Exception as e:
        logger.error(f"Error running indexer: {str(e)}")
        ray.shutdown()
        sys.exit(1)

if __name__ == "__main__":
    main()
