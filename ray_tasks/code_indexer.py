#!/usr/bin/env python3
"""
Code Indexer for Ray cluster - indexes and tags source code files 
to enable quick searching and code navigation.
"""

import os
import re
import json
import hashlib
import logging
from typing import Dict, List, Any, Optional, Set, Tuple
import time
from pathlib import Path
import ray

from .error_handling import retry, with_ray_error_handling, track_errors
from .resource_utils import get_optimal_resource_allocation

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Define supported languages and their extensions
SUPPORTED_LANGUAGES = {
    "python": [".py", ".pyi", ".pyx"],
    "javascript": [".js", ".jsx", ".ts", ".tsx"],
    "java": [".java"],
    "cpp": [".cpp", ".hpp", ".cc", ".cxx", ".h", ".c"],
    "go": [".go"],
    "rust": [".rs"],
    "ruby": [".rb"],
    "php": [".php"],
    "csharp": [".cs"],
    "swift": [".swift"],
    "kotlin": [".kt"],
    "shell": [".sh", ".bash"],
    "html": [".html", ".htm"],
    "css": [".css", ".scss", ".sass", ".less"],
    "yaml": [".yml", ".yaml"],
    "json": [".json"],
    "markdown": [".md", ".markdown"],
}

# Define patterns for entity extraction
PATTERNS = {
    "python": {
        "class": r"class\s+(\w+)(?:\(.*\))?:",
        "function": r"def\s+(\w+)\s*\(",
        "variable": r"^(\w+)\s*=",
        "import": r"(?:import|from)\s+([\w\.]+)",
    },
    "javascript": {
        "class": r"class\s+(\w+)(?:\s+extends\s+\w+)?(?:\s+implements\s+[\w,\s]+)?\s*{",
        "function": r"(?:function\s+(\w+)|const\s+(\w+)\s*=\s*(?:async\s*)?\(|(\w+)\s*:\s*(?:async\s*)?\()",
        "variable": r"(?:const|let|var)\s+(\w+)\s*=",
        "import": r"import\s+(?:{[^}]*}|[^{]*)\s+from\s+['\"]([^'\"]+)['\"]",
    },
    # Add patterns for other languages as needed
}

@ray.remote
@track_errors
@with_ray_error_handling
def index_file(file_path: str, file_content: Optional[str] = None) -> Dict[str, Any]:
    """
    Index a single file to extract code entities and metadata
    
    Args:
        file_path: Path to the file to index
        file_content: Optional file content (if already loaded)
        
    Returns:
        Dictionary containing indexed data
    """
    try:
        # Get file extension and determine language
        _, ext = os.path.splitext(file_path)
        language = None
        
        for lang, extensions in SUPPORTED_LANGUAGES.items():
            if ext.lower() in extensions:
                language = lang
                break
        
        if not language:
            return {
                "path": file_path,
                "status": "skipped",
                "reason": f"Unsupported file type: {ext}"
            }
        
        # Read file if content not provided
        if file_content is None:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    file_content = f.read()
            except UnicodeDecodeError:
                # Try with latin-1 encoding as fallback
                with open(file_path, 'r', encoding='latin-1') as f:
                    file_content = f.read()
        
        # Calculate file hash for change detection
        file_hash = hashlib.md5(file_content.encode('utf-8')).hexdigest()
        
        # Extract entities based on language patterns
        entities = extract_entities(file_content, language)
        
        # Calculate code metrics
        metrics = calculate_metrics(file_content, language)
        
        # Create document objects for full-text search
        # In a real implementation, this might feed into Elasticsearch, 
        # SQLite FTS, or another search engine
        document = {
            "path": file_path,
            "content": file_content,
            "language": language,
            "hash": file_hash,
            "entities": entities,
            "metrics": metrics,
            "last_indexed": time.time(),
            "status": "success"
        }
        
        return document
        
    except Exception as e:
        logger.error(f"Error indexing file {file_path}: {str(e)}")
        return {
            "path": file_path,
            "status": "error",
            "error": str(e)
        }

def extract_entities(content: str, language: str) -> Dict[str, List[Dict[str, Any]]]:
    """
    Extract code entities from file content based on language patterns
    
    Args:
        content: File content as a string
        language: Programming language
        
    Returns:
        Dictionary of entity types and their occurrences
    """
    if language not in PATTERNS:
        return {}
    
    entities = {
        "classes": [],
        "functions": [],
        "variables": [],
        "imports": []
    }
    
    lines = content.split('\n')
    
    # Extract classes
    if "class" in PATTERNS[language]:
        for i, line in enumerate(lines):
            matches = re.findall(PATTERNS[language]["class"], line)
            for match in matches:
                entities["classes"].append({
                    "name": match,
                    "line": i + 1,
                    "code_context": line.strip()
                })
    
    # Extract functions
    if "function" in PATTERNS[language]:
        for i, line in enumerate(lines):
            matches = re.findall(PATTERNS[language]["function"], line)
            for match_tuple in matches:
                # Handle multiple capture groups in regex
                match = next((m for m in match_tuple if m), None)
                if match:
                    entities["functions"].append({
                        "name": match,
                        "line": i + 1,
                        "code_context": line.strip()
                    })
    
    # Extract variables
    if "variable" in PATTERNS[language]:
        for i, line in enumerate(lines):
            matches = re.findall(PATTERNS[language]["variable"], line)
            for match in matches:
                entities["variables"].append({
                    "name": match,
                    "line": i + 1,
                    "code_context": line.strip()
                })
    
    # Extract imports
    if "import" in PATTERNS[language]:
        for i, line in enumerate(lines):
            matches = re.findall(PATTERNS[language]["import"], line)
            for match in matches:
                entities["imports"].append({
                    "module": match,
                    "line": i + 1,
                    "code_context": line.strip()
                })
    
    return entities

def calculate_metrics(content: str, language: str) -> Dict[str, Any]:
    """
    Calculate code metrics like line count, complexity, etc.
    
    Args:
        content: File content as a string
        language: Programming language
        
    Returns:
        Dictionary of metrics
    """
    lines = content.split('\n')
    
    # Count non-empty lines
    non_empty_lines = sum(1 for line in lines if line.strip())
    
    # Count comment lines (simplified)
    comment_markers = {
        "python": "#",
        "javascript": "//",
    }
    
    comment_count = 0
    if language in comment_markers:
        marker = comment_markers[language]
        comment_count = sum(1 for line in lines if line.strip().startswith(marker))
    
    # Simplified cyclomatic complexity (very rough approximation)
    # A proper implementation would use a language-specific parser
    complexity_markers = [
        "if ", "else:", "elif ", "for ", "while ", "try:", "except:",
        "switch", "case", "&&", "||", "?", "catch", "finally"
    ]
    
    complexity_count = 0
    for marker in complexity_markers:
        complexity_count += content.count(marker)
    
    return {
        "total_lines": len(lines),
        "code_lines": non_empty_lines - comment_count,
        "comment_lines": comment_count,
        "blank_lines": len(lines) - non_empty_lines,
        "approximate_complexity": complexity_count
    }

@ray.remote
def build_index(
    base_dir: str,
    output_file: Optional[str] = None,
    file_extensions: Optional[List[str]] = None,
    exclude_dirs: Optional[List[str]] = None,
    batch_size: int = 50
) -> Dict[str, Any]:
    """
    Build a code index for an entire directory structure
    
    Args:
        base_dir: Base directory to start indexing from
        output_file: Optional path to save the index
        file_extensions: Optional list of file extensions to include
        exclude_dirs: Optional list of directories to exclude
        batch_size: Number of files to process in each batch
        
    Returns:
        Summary statistics about the indexing process
    """
    if exclude_dirs is None:
        exclude_dirs = ['.git', 'node_modules', 'venv', '__pycache__', 'build', 'dist']
        
    if file_extensions is None:
        # Flatten the list of supported extensions
        file_extensions = [ext for exts in SUPPORTED_LANGUAGES.values() for ext in exts]
    
    # Scan the directory for files to index
    files_to_index = []
    for root, dirs, files in os.walk(base_dir):
        # Skip excluded directories
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        
        for file in files:
            _, ext = os.path.splitext(file)
            if ext.lower() in file_extensions:
                file_path = os.path.join(root, file)
                files_to_index.append(file_path)
    
    if not files_to_index:
        logger.warning(f"No files found to index in {base_dir}")
        return {"status": "error", "message": "No files found to index"}
    
    # Create batches
    file_batches = [files_to_index[i:i+batch_size] for i in range(0, len(files_to_index), batch_size)]
    
    # Process each batch in parallel
    start_time = time.time()
    all_results = []
    
    for batch in file_batches:
        # Create remote tasks for this batch
        tasks = []
        for file_path in batch:
            # Allocate resources based on file size
            file_size = os.path.getsize(file_path)
            resources = get_optimal_resource_allocation(task_type="cpu_intensive", file_size=file_size)
            
            # Create optimized remote task
            optimized_task = ray.remote(**resources)(index_file)
            tasks.append(optimized_task.remote(file_path))
        
        # Get results from this batch
        batch_results = ray.get(tasks)
        all_results.extend(batch_results)
        
        logger.info(f"Processed batch of {len(batch)} files")
    
    # Build the index from results
    indexed_files = {}
    error_count = 0
    success_count = 0
    skipped_count = 0
    
    for result in all_results:
        path = result.get("path", "unknown")
        
        if result.get("status") == "success":
            indexed_files[path] = result
            success_count += 1
        elif result.get("status") == "skipped":
            skipped_count += 1
        else:
            error_count += 1
    
    total_time = time.time() - start_time
    
    # Build the final index
    index = {
        "metadata": {
            "timestamp": time.time(),
            "base_dir": base_dir,
            "total_files": len(files_to_index),
            "indexed_files": success_count,
            "skipped_files": skipped_count,
            "error_files": error_count,
            "total_time_seconds": total_time
        },
        "files": indexed_files
    }
    
    # Save to file if requested
    if output_file:
        os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(index, f, indent=2)
        logger.info(f"Index saved to {output_file}")
    
    return {
        "status": "success",
        "total_files": len(files_to_index),
        "indexed_files": success_count,
        "skipped_files": skipped_count,
        "error_files": error_count,
        "total_time_seconds": total_time
    }

# Tag-based search functions
def search_files_by_tags(
    index: Dict[str, Any],
    tags: List[str],
    match_all: bool = True
) -> List[str]:
    """
    Search for files that match specified tags
    
    Args:
        index: The code index
        tags: List of tags to search for
        match_all: If True, match all tags; if False, match any tag
        
    Returns:
        List of file paths that match the tags
    """
    matched_files = []
    
    for file_path, file_data in index.get("files", {}).items():
        # Skip files that weren't successfully indexed
        if file_data.get("status") != "success":
            continue
        
        # Check if the file has the required tags
        file_tags = set()
        
        # Add language as a tag
        file_tags.add(f"lang:{file_data.get('language', '')}")
        
        # Add entity-based tags
        entities = file_data.get("entities", {})
        for entity_type, entity_list in entities.items():
            for entity in entity_list:
                if "name" in entity:
                    file_tags.add(f"{entity_type[:-1]}:{entity['name']}")
                elif "module" in entity:
                    file_tags.add(f"import:{entity['module']}")
        
        # Check if the file matches the requested tags
        if match_all:
            if all(tag in file_tags for tag in tags):
                matched_files.append(file_path)
        else:
            if any(tag in file_tags for tag in tags):
                matched_files.append(file_path)
    
    return matched_files

def find_related_files(
    index: Dict[str, Any],
    file_path: str,
    max_results: int = 10
) -> List[Tuple[str, float]]:
    """
    Find files related to a given file based on shared entities
    
    Args:
        index: The code index
        file_path: Path to the file to find related files for
        max_results: Maximum number of results to return
        
    Returns:
        List of (file_path, score) tuples for related files
    """
    if file_path not in index.get("files", {}):
        return []
    
    target_file = index["files"][file_path]
    
    # Skip files that weren't successfully indexed
    if target_file.get("status") != "success":
        return []
    
    # Get entities from the target file
    target_entities = target_file.get("entities", {})
    
    # Extract all entity names and imports
    target_classes = {entity["name"] for entity in target_entities.get("classes", [])}
    target_functions = {entity["name"] for entity in target_entities.get("functions", [])}
    target_imports = {entity["module"] for entity in target_entities.get("imports", [])}
    
    # Track scores for each file
    scores = {}
    
    for path, file_data in index.get("files", {}).items():
        # Skip the target file itself and files that weren't successfully indexed
        if path == file_path or file_data.get("status") != "success":
            continue
        
        # Get entities from this file
        entities = file_data.get("entities", {})
        
        # Check for shared classes, functions, and imports
        file_classes = {entity["name"] for entity in entities.get("classes", [])}
        file_functions = {entity["name"] for entity in entities.get("functions", [])}
        file_imports = {entity["module"] for entity in entities.get("imports", [])}
        
        # Calculate a similarity score
        score = 0
        
        # Shared classes are a strong signal
        shared_classes = target_classes.intersection(file_classes)
        score += len(shared_classes) * 5
        
        # Shared functions are also important
        shared_functions = target_functions.intersection(file_functions)
        score += len(shared_functions) * 3
        
        # Shared imports indicate related functionality
        shared_imports = target_imports.intersection(file_imports)
        score += len(shared_imports)
        
        # Only include files with non-zero scores
        if score > 0:
            scores[path] = score
    
    # Sort by score and return the top results
    related_files = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return related_files[:max_results]

if __name__ == "__main__":
    # Example usage
    import argparse
    
    parser = argparse.ArgumentParser(description="Code Indexer")
    parser.add_argument("--dir", type=str, required=True, help="Directory to index")
    parser.add_argument("--output", type=str, default="code_index.json", help="Output index file")
    args = parser.parse_args()
    
    # Initialize Ray
    ray.init(ignore_reinit_error=True)
    
    # Run the indexer
    result = build_index.remote(
        base_dir=args.dir,
        output_file=args.output
    )
    
    # Wait for the result
    final_result = ray.get(result)
    
    print(f"Indexing completed: {final_result['indexed_files']} files indexed, "
          f"{final_result['error_files']} errors, {final_result['skipped_files']} skipped")
    print(f"Total time: {final_result['total_time_seconds']:.2f} seconds")
