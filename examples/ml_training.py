#!/usr/bin/env python3
"""
Example of distributed machine learning training using the Ray cluster.
This example demonstrates how to train multiple models in parallel
and perform hyperparameter optimization across a Ray cluster.
"""

import os
import sys
import time
import argparse
import logging
import pickle
from typing import Dict, List, Any, Tuple, Optional
import numpy as np
import ray
from sklearn.datasets import load_digits, load_wine, load_breast_cancer
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.svm import SVC
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score
from sklearn.model_selection import ParameterGrid

# Add the project root to the Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, project_root)

from ray_tasks.task_manager import distribute_tasks, execute_in_parallel
from ray_tasks.error_handling import retry, with_ray_error_handling, track_errors

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Available datasets and models for the example
AVAILABLE_DATASETS = {
    "digits": load_digits,
    "wine": load_wine,
    "breast_cancer": load_breast_cancer
}

AVAILABLE_MODELS = {
    "random_forest": RandomForestClassifier,
    "gradient_boosting": GradientBoostingClassifier,
    "svm": SVC
}

# Example hyperparameter grid for each model
DEFAULT_HYPERPARAMS = {
    "random_forest": {
        "n_estimators": [50, 100, 200],
        "max_depth": [None, 5, 10, 15],
        "min_samples_split": [2, 5, 10]
    },
    "gradient_boosting": {
        "n_estimators": [50, 100, 200],
        "learning_rate": [0.01, 0.1, 0.2],
        "max_depth": [3, 5, 7]
    },
    "svm": {
        "C": [0.1, 1.0, 10.0],
        "kernel": ["linear", "rbf"],
        "gamma": ["scale", "auto", 0.1]
    }
}

@ray.remote
@track_errors
@with_ray_error_handling
def train_model(
    model_config: Dict[str, Any],
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_test: np.ndarray,
    y_test: np.ndarray
) -> Dict[str, Any]:
    """
    Train a single model with a specific configuration and evaluate it
    
    Args:
        model_config: Dictionary containing model type and hyperparameters
        X_train: Training features
        y_train: Training labels
        X_test: Test features
        y_test: Test labels
        
    Returns:
        Dictionary with model, configuration, and performance metrics
    """
    start_time = time.time()
    
    try:
        model_type = model_config["model_type"]
        hyperparams = model_config["hyperparams"]
        
        # Log training start
        param_str = ", ".join(f"{k}={v}" for k, v in hyperparams.items())
        logger.debug(f"Training {model_type} with params: {param_str}")
        
        # Create and train the model
        model_class = AVAILABLE_MODELS[model_type]
        model = model_class(**hyperparams)
        
        # Fit the model
        model.fit(X_train, y_train)
        
        # Get predictions
        y_pred = model.predict(X_test)
        
        # Calculate metrics
        metrics = {
            "accuracy": float(accuracy_score(y_test, y_pred)),
            "precision": float(precision_score(y_test, y_pred, average="weighted")),
            "recall": float(recall_score(y_test, y_pred, average="weighted")),
            "f1": float(f1_score(y_test, y_pred, average="weighted"))
        }
        
        # Calculate training time
        training_time = time.time() - start_time
        
        result = {
            "model_type": model_type,
            "hyperparams": hyperparams,
            "metrics": metrics,
            "training_time": training_time,
            "model": model,
            "status": "success"
        }
        
        return result
        
    except Exception as e:
        logger.error(f"Error training model: {str(e)}")
        training_time = time.time() - start_time
        
        return {
            "model_type": model_config.get("model_type", "unknown"),
            "hyperparams": model_config.get("hyperparams", {}),
            "error": str(e),
            "training_time": training_time,
            "status": "error"
        }

def generate_hyperparameter_configs(
    model_types: List[str],
    custom_param_grid: Optional[Dict[str, Dict[str, List[Any]]]] = None
) -> List[Dict[str, Any]]:
    """
    Generate all combinations of hyperparameters to try
    
    Args:
        model_types: List of model types to include
        custom_param_grid: Optional custom parameter grid
        
    Returns:
        List of model configurations
    """
    configs = []
    
    # Use custom param grid if provided, otherwise use defaults
    param_grid = custom_param_grid or DEFAULT_HYPERPARAMS
    
    for model_type in model_types:
        if model_type not in AVAILABLE_MODELS:
            logger.warning(f"Unknown model type: {model_type}")
            continue
            
        # Get the hyperparameter grid for this model
        model_param_grid = param_grid.get(model_type, DEFAULT_HYPERPARAMS[model_type])
        
        # Generate all combinations
        for hyperparams in ParameterGrid(model_param_grid):
            configs.append({
                "model_type": model_type,
                "hyperparams": hyperparams
            })
    
    return configs

def load_dataset(dataset_name: str) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Load and prepare a dataset for training
    
    Args:
        dataset_name: Name of the dataset to load
        
    Returns:
        X_train, X_test, y_train, y_test
    """
    if dataset_name not in AVAILABLE_DATASETS:
        raise ValueError(f"Unknown dataset: {dataset_name}")
    
    # Load the dataset
    dataset = AVAILABLE_DATASETS[dataset_name]()
    X, y = dataset.data, dataset.target
    
    # Split into train and test sets
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.25, random_state=42)
    
    # Scale the features
    scaler = StandardScaler()
    X_train = scaler.fit_transform(X_train)
    X_test = scaler.transform(X_test)
    
    return X_train, X_test, y_train, y_test

def distributed_hyperparameter_optimization(
    dataset_name: str,
    model_types: List[str] = ["random_forest", "gradient_boosting", "svm"],
    custom_param_grid: Optional[Dict[str, Dict[str, List[Any]]]] = None,
    n_top_models: int = 5,
    save_models: bool = False,
    output_dir: Optional[str] = None
) -> Dict[str, Any]:
    """
    Perform distributed hyperparameter optimization
    
    Args:
        dataset_name: Name of the dataset to use
        model_types: List of model types to train
        custom_param_grid: Optional custom parameter grid
        n_top_models: Number of top models to return
        save_models: Whether to save the trained models
        output_dir: Directory to save models to
        
    Returns:
        Dictionary with results
    """
    # Initialize Ray if not already
    if not ray.is_initialized():
        try:
            ray.init(address="auto", ignore_reinit_error=True)
            logger.info("Connected to existing Ray cluster")
        except ConnectionError:
            ray.init(ignore_reinit_error=True)
            logger.info("Started new local Ray instance")
    
    # Load dataset
    logger.info(f"Loading dataset: {dataset_name}")
    X_train, X_test, y_train, y_test = load_dataset(dataset_name)
    
    # Generate model configurations
    configs = generate_hyperparameter_configs(model_types, custom_param_grid)
    logger.info(f"Generated {len(configs)} model configurations to evaluate")
    
    # Create a reference to the training data that all tasks can access
    # Note: For large datasets, you might want to use Ray's data handling facilities
    # This simple approach works for small datasets
    X_train_ref = ray.put(X_train)
    y_train_ref = ray.put(y_train)
    X_test_ref = ray.put(X_test)
    y_test_ref = ray.put(y_test)
    
    # Define progress callback
    def report_progress(completed, total):
        logger.info(f"Progress: {completed}/{total} models trained ({completed/total*100:.1f}%)")
    
    # Distribute the training tasks
    logger.info("Starting distributed model training")
    start_time = time.time()
    
    results = distribute_tasks(
        task_func=train_model,
        items=configs,
        task_type="cpu_intensive",
        progress_callback=report_progress,
        memory_intensive=True
    )
    
    total_time = time.time() - start_time
    
    # Count successes and failures
    successful_results = [r for r in results if r.get("status") == "success"]
    failed_results = [r for r in results if r.get("status") == "error"]
    
    logger.info(f"Completed {len(results)} model trainings in {total_time:.2f} seconds")
    logger.info(f"Successful: {len(successful_results)}, Failed: {len(failed_results)}")
    
    # Sort by accuracy
    if successful_results:
        successful_results.sort(key=lambda x: x["metrics"]["accuracy"], reverse=True)
        
        # Get top N models
        top_models = successful_results[:n_top_models]
        
        logger.info("\nTop Models:")
        for i, model in enumerate(top_models):
            logger.info(f"{i+1}. {model['model_type']} - "
                      f"Accuracy: {model['metrics']['accuracy']:.4f}, "
                      f"F1: {model['metrics']['f1']:.4f}, "
                      f"Params: {', '.join(f'{k}={v}' for k, v in model['hyperparams'].items())}")
        
        # Save models if requested
        if save_models and output_dir:
            os.makedirs(output_dir, exist_ok=True)
            for i, model in enumerate(top_models):
                model_path = os.path.join(
                    output_dir, 
                    f"{i+1}_{model['model_type']}_{model['metrics']['accuracy']:.4f}.pkl"
                )
                with open(model_path, 'wb') as f:
                    pickle.dump(model['model'], f)
            logger.info(f"Saved top {len(top_models)} models to {output_dir}")
    
    # Return summary
    return {
        "status": "success",
        "models_trained": len(results),
        "successful": len(successful_results),
        "failed": len(failed_results),
        "top_models": top_models if successful_results else [],
        "total_time": total_time,
        "parallel_speedup": sum(r.get("training_time", 0) for r in results) / max(total_time, 0.001),
        "dataset": dataset_name
    }

# Command-line interface
def main():
    parser = argparse.ArgumentParser(description="Distributed ML model training example")
    parser.add_argument("--dataset", choices=list(AVAILABLE_DATASETS.keys()), default="digits",
                       help="Dataset to use for training")
    parser.add_argument("--models", nargs="+", choices=list(AVAILABLE_MODELS.keys()),
                       default=["random_forest", "gradient_boosting", "svm"],
                       help="Model types to train")
    parser.add_argument("--top", type=int, default=3,
                       help="Number of top models to display")
    parser.add_argument("--save", action="store_true",
                       help="Save the top models")
    parser.add_argument("--output-dir", type=str, default="./models",
                       help="Directory to save models to")
    args = parser.parse_args()
    
    # Run the distributed hyperparameter optimization
    results = distributed_hyperparameter_optimization(
        dataset_name=args.dataset,
        model_types=args.models,
        n_top_models=args.top,
        save_models=args.save,
        output_dir=args.output_dir
    )
    
    # Shutdown Ray
    ray.shutdown()

if __name__ == "__main__":
    main()
