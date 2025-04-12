#!/usr/bin/env python3
"""
Error handling utilities for Ray cluster - provides robust error handling, 
retry mechanisms, and error reporting.
"""

import functools
import logging
import time
import traceback
import ray
from typing import Any, Callable, Dict, List, Optional, Union, TypeVar

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Type variable for generic function types
F = TypeVar('F', bound=Callable[..., Any])

class RayTaskError(Exception):
    """Base exception for Ray task errors"""
    def __init__(self, message: str, task_id: Optional[str] = None, 
                 node_id: Optional[str] = None, cause: Optional[Exception] = None):
        self.message = message
        self.task_id = task_id
        self.node_id = node_id
        self.cause = cause
        super().__init__(self.format_error())
        
    def format_error(self) -> str:
        """Format error message with relevant details"""
        error = f"Ray Task Error: {self.message}"
        if self.task_id:
            error += f" (Task ID: {self.task_id})"
        if self.node_id:
            error += f" (Node ID: {self.node_id})"
        if self.cause:
            error += f"\nCaused by: {type(self.cause).__name__}: {str(self.cause)}"
        return error

class ResourceError(RayTaskError):
    """Error raised when there are insufficient resources"""
    pass

class NetworkError(RayTaskError):
    """Error raised when there are network communication issues"""
    pass

class TaskTimeoutError(RayTaskError):
    """Error raised when a task times out"""
    pass

def with_ray_error_handling(func: F) -> F:
    """
    Decorator to handle Ray-specific errors and provide better error messages
    
    Args:
        func: The function to decorate
        
    Returns:
        Decorated function with enhanced error handling
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except ray.exceptions.RayTaskError as e:
            # Extract cause from Ray task error
            cause = e.__cause__ if hasattr(e, '__cause__') else None
            raise RayTaskError("Task execution failed", cause=cause) from e
        except ray.exceptions.GetTimeoutError:
            raise TaskTimeoutError("Task timed out - consider increasing timeout value or check for resource bottlenecks")
        except ray.exceptions.RaySystemError as e:
            raise NetworkError(f"Ray system error: {str(e)}")
        except ray.exceptions.RayOutOfMemoryError:
            raise ResourceError("Out of memory error - reduce batch size or allocate more memory")
        except ray.exceptions.RayActorError as e:
            raise RayTaskError(f"Actor failed: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}\n{traceback.format_exc()}")
            raise
    
    return wrapper  # type: ignore

def retry(max_attempts: int = 3, 
          delay_seconds: int = 2, 
          backoff_factor: float = 1.5,
          exceptions: tuple = (Exception,),
          on_retry: Optional[Callable[[Exception, int], None]] = None) -> Callable:
    """
    Retry decorator with exponential backoff
    
    Args:
        max_attempts: Maximum number of retry attempts
        delay_seconds: Initial delay between retries in seconds
        backoff_factor: Multiplier for the delay after each retry
        exceptions: Tuple of exceptions to catch for retry
        on_retry: Optional callback function called after each retry
        
    Returns:
        Decorated function with retry logic
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            attempt = 0
            current_delay = delay_seconds
            
            while attempt < max_attempts:
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    attempt += 1
                    if attempt >= max_attempts:
                        logger.error(
                            f"Function {func.__name__} failed after {max_attempts} attempts. "
                            f"Last error: {str(e)}"
                        )
                        raise
                    
                    # Log the retry attempt
                    logger.warning(
                        f"Retry {attempt}/{max_attempts-1} for {func.__name__} "
                        f"after error: {str(e)}. "
                        f"Retrying in {current_delay}s"
                    )
                    
                    # Call on_retry callback if provided
                    if on_retry:
                        on_retry(e, attempt)
                        
                    # Wait before next attempt
                    time.sleep(current_delay)
                    
                    # Increase delay for next attempt
                    current_delay = current_delay * backoff_factor
        
        return wrapper  # type: ignore
    
    return decorator

def capture_task_errors(results: List[Any]) -> Dict[str, List]:
    """
    Process Ray task results and separate successful results from errors
    
    Args:
        results: List of results from Ray tasks
        
    Returns:
        Dictionary with 'successful' and 'failed' lists
    """
    successful = []
    failed = []
    
    for result in results:
        if isinstance(result, dict) and 'error' in result:
            failed.append(result)
        elif result is None:
            failed.append({'error': 'Task returned None (possible timeout)'})
        else:
            successful.append(result)
    
    return {
        'successful': successful,
        'failed': failed,
        'success_count': len(successful),
        'failure_count': len(failed),
        'total': len(results),
        'success_rate': len(successful) / len(results) if results else 0
    }

def handle_task_errors(results: Dict[str, List]) -> None:
    """
    Log statistics about task results
    
    Args:
        results: Dictionary from capture_task_errors
    """
    logger.info(
        f"Task Summary: {results['success_count']}/{results['total']} successful "
        f"({results['success_rate']*100:.1f}% success rate)"
    )
    
    if results['failure_count'] > 0:
        logger.warning(f"Failed tasks: {results['failure_count']}")
        for i, failure in enumerate(results['failed']):
            error_msg = failure.get('error', 'Unknown error')
            logger.warning(f"  Failed task {i+1}: {error_msg}")

# Custom exception tracker to monitor and report on frequent errors
class ErrorTracker:
    def __init__(self):
        self.error_counts = {}
        self.total_tasks = 0
        self.failed_tasks = 0
    
    def record_success(self):
        """Record a successful task"""
        self.total_tasks += 1
    
    def record_error(self, error_type: str, error_message: str):
        """Record an error occurrence"""
        self.total_tasks += 1
        self.failed_tasks += 1
        
        if error_type not in self.error_counts:
            self.error_counts[error_type] = {
                'count': 0,
                'messages': {}
            }
        
        self.error_counts[error_type]['count'] += 1
        
        if error_message not in self.error_counts[error_type]['messages']:
            self.error_counts[error_type]['messages'][error_message] = 0
        
        self.error_counts[error_type]['messages'][error_message] += 1
    
    def get_report(self) -> Dict[str, Any]:
        """Get error statistics report"""
        return {
            'total_tasks': self.total_tasks,
            'failed_tasks': self.failed_tasks,
            'success_rate': (self.total_tasks - self.failed_tasks) / self.total_tasks if self.total_tasks else 0,
            'error_types': {
                error_type: {
                    'count': data['count'],
                    'percentage': data['count'] / self.failed_tasks if self.failed_tasks else 0,
                    'most_common_message': max(
                        data['messages'].items(), 
                        key=lambda x: x[1]
                    )[0] if data['messages'] else None
                }
                for error_type, data in self.error_counts.items()
            }
        }

# Initialize a global error tracker
error_tracker = ErrorTracker()

def track_errors(func: F) -> F:
    """
    Decorator to track errors in the global error tracker
    
    Args:
        func: The function to decorate
        
    Returns:
        Decorated function that tracks errors
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            result = func(*args, **kwargs)
            error_tracker.record_success()
            return result
        except Exception as e:
            error_type = type(e).__name__
            error_message = str(e)
            error_tracker.record_error(error_type, error_message)
            raise
    
    return wrapper  # type: ignore
