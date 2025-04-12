#!/usr/bin/env python3
"""
Claude API proxy for Ray cluster - provides a distributed proxy for Claude API calls,
with proper error handling, retries, and OpenAI-compatible API endpoints.
"""

import os
import time
import logging
from typing import Dict, List, Any, Optional, Union
import json
import ray
import requests
from dotenv import load_dotenv

from .error_handling import retry, with_ray_error_handling, track_errors

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Get API key from environment
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
if not ANTHROPIC_API_KEY:
    logger.warning("ANTHROPIC_API_KEY not found in environment variables")

# Default model to use
DEFAULT_MODEL = os.getenv("CLAUDE_MODEL", "claude-3-7-sonnet-20250219")

# Anthropic API endpoints
ANTHROPIC_API_URL = "https://api.anthropic.com/v1"

class ClaudeAPIError(Exception):
    """Exception for Claude API errors"""
    def __init__(self, message: str, status_code: Optional[int] = None, response: Optional[Dict] = None):
        self.message = message
        self.status_code = status_code
        self.response = response
        super().__init__(self.message)

@ray.remote
@track_errors
@with_ray_error_handling
@retry(max_attempts=3, delay_seconds=1, backoff_factor=2, 
       exceptions=(requests.RequestException, ClaudeAPIError))
def claude_completion(
    prompt: str,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 1024,
    temperature: float = 0.7,
    top_p: float = 0.9,
    system_prompt: Optional[str] = None,
    stop_sequences: Optional[List[str]] = None,
    timeout: int = 30,
    api_key: Optional[str] = None
) -> Dict[str, Any]:
    """
    Call Claude API for text completion with retry logic
    
    Args:
        prompt: User's prompt text
        model: Claude model to use
        max_tokens: Maximum tokens to generate
        temperature: Temperature for sampling
        top_p: Top-p sampling parameter
        system_prompt: Optional system prompt
        stop_sequences: Optional list of stop sequences
        timeout: Request timeout in seconds
        api_key: Optional API key (defaults to environment variable)
        
    Returns:
        Parsed JSON response from Claude API
    """
    api_key = api_key or ANTHROPIC_API_KEY
    if not api_key:
        raise ClaudeAPIError("No API key provided and none found in environment")
    
    headers = {
        "anthropic-version": "2023-06-01",
        "x-api-key": api_key,
        "content-type": "application/json"
    }
    
    # Create the message list
    messages = [{"role": "user", "content": prompt}]
    
    # Prepare request payload
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
    }
    
    # Add system prompt if provided
    if system_prompt:
        payload["system"] = system_prompt
    
    # Add stop sequences if provided
    if stop_sequences:
        payload["stop_sequences"] = stop_sequences
    
    start_time = time.time()
    logger.debug(f"Sending request to Claude API: {json.dumps(payload)[:200]}...")
    
    try:
        response = requests.post(
            f"{ANTHROPIC_API_URL}/messages",
            headers=headers,
            json=payload,
            timeout=timeout
        )
        
        elapsed_time = time.time() - start_time
        logger.debug(f"Claude API request completed in {elapsed_time:.2f}s")
        
        response.raise_for_status()
        result = response.json()
        
        # Convert the raw response to a more standardized format
        content = result.get("content", [])
        text = ""
        for block in content:
            if block.get("type") == "text":
                text += block.get("text", "")
        
        return {
            "id": result.get("id", ""),
            "model": result.get("model", ""),
            "created": result.get("created_at", 0),
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": text
                },
                "finish_reason": result.get("stop_reason", "stop"),
                "index": 0
            }],
            "usage": result.get("usage", {})
        }
        
    except requests.exceptions.RequestException as e:
        # Handle network/connection errors
        logger.error(f"Request to Claude API failed: {str(e)}")
        raise ClaudeAPIError(f"Request failed: {str(e)}")
    
    except json.JSONDecodeError:
        # Handle invalid JSON response
        logger.error("Invalid JSON response from Claude API")
        raise ClaudeAPIError("Invalid JSON response", 
                             status_code=response.status_code if 'response' in locals() else None)
    
    except Exception as e:
        # Handle any other errors
        logger.error(f"Unexpected error in Claude API call: {str(e)}")
        raise ClaudeAPIError(f"Unexpected error: {str(e)}")

@ray.remote
@track_errors
@with_ray_error_handling
@retry(max_attempts=2, delay_seconds=1)
def batch_claude_completion(
    prompts: List[str],
    model: str = DEFAULT_MODEL,
    max_tokens: int = 1024,
    temperature: float = 0.7,
    system_prompt: Optional[str] = None,
    api_key: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Batch process multiple prompts with Claude API
    
    Args:
        prompts: List of prompts to process
        model: Claude model to use
        max_tokens: Maximum tokens to generate per prompt
        temperature: Temperature for sampling
        system_prompt: Optional system prompt
        api_key: Optional API key (defaults to environment variable)
        
    Returns:
        List of Claude API responses
    """
    results = []
    for prompt in prompts:
        try:
            result = claude_completion.remote(
                prompt=prompt,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system_prompt=system_prompt,
                api_key=api_key
            )
            results.append(result)
        except Exception as e:
            logger.error(f"Error processing prompt: {str(e)}")
            results.append(ray.put({
                "error": str(e),
                "prompt": prompt[:100] + "..." if len(prompt) > 100 else prompt
            }))
    
    return ray.get(results)

@ray.remote
class ClaudeAPIActor:
    """Ray Actor for handling Claude API requests with rate limiting"""
    
    def __init__(self, api_key: Optional[str] = None, rate_limit_per_minute: int = 50):
        self.api_key = api_key or ANTHROPIC_API_KEY
        self.rate_limit_per_minute = rate_limit_per_minute
        self.last_request_times = []
        self.total_requests = 0
        self.failed_requests = 0
    
    def _respect_rate_limit(self):
        """Enforce rate limiting by waiting if necessary"""
        current_time = time.time()
        
        # Remove timestamps older than 1 minute
        self.last_request_times = [t for t in self.last_request_times 
                                   if current_time - t < 60]
        
        # If we've hit the rate limit, wait
        if len(self.last_request_times) >= self.rate_limit_per_minute:
            # Calculate time to wait
            oldest_relevant_time = self.last_request_times[0]
            time_to_wait = 60 - (current_time - oldest_relevant_time)
            
            if time_to_wait > 0:
                logger.info(f"Rate limit reached. Waiting {time_to_wait:.2f}s")
                time.sleep(time_to_wait)
        
        # Add current request time
        self.last_request_times.append(time.time())
    
    @with_ray_error_handling
    def completion(self, prompt: str, **kwargs):
        """Process a single completion request with rate limiting"""
        self._respect_rate_limit()
        self.total_requests += 1
        
        try:
            return claude_completion(
                prompt=prompt,
                api_key=self.api_key,
                **kwargs
            )
        except Exception as e:
            self.failed_requests += 1
            raise
    
    def get_stats(self):
        """Get current stats for this actor"""
        return {
            "total_requests": self.total_requests,
            "failed_requests": self.failed_requests,
            "success_rate": (self.total_requests - self.failed_requests) / self.total_requests 
                            if self.total_requests > 0 else 0,
            "current_rate": len(self.last_request_times),
            "rate_limit": self.rate_limit_per_minute
        }

# OpenAI compatibility functions
def openai_to_claude_prompt(payload: Dict) -> Dict:
    """
    Convert OpenAI API format to Claude API format
    
    Args:
        payload: OpenAI-formatted request payload
        
    Returns:
        Claude-formatted request payload
    """
    claude_payload = {}
    
    # Map model
    if "model" in payload:
        claude_payload["model"] = DEFAULT_MODEL  # Always use configured Claude model
    
    # Map messages
    if "messages" in payload:
        messages = payload["messages"]
        prompt = ""
        system_prompt = None
        
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            
            if role == "system":
                system_prompt = content
            elif role == "user":
                prompt += f"{content}\n"
            elif role == "assistant":
                prompt += f"Assistant: {content}\n\nUser: "
        
        claude_payload["prompt"] = prompt
        if system_prompt:
            claude_payload["system_prompt"] = system_prompt
    
    # Map other parameters
    claude_payload["max_tokens"] = payload.get("max_tokens", 1024)
    claude_payload["temperature"] = payload.get("temperature", 0.7)
    
    # Map any stop sequences
    if "stop" in payload:
        if isinstance(payload["stop"], list):
            claude_payload["stop_sequences"] = payload["stop"]
        else:
            claude_payload["stop_sequences"] = [payload["stop"]]
    
    return claude_payload

def claude_to_openai_response(claude_response: Dict) -> Dict:
    """
    Convert Claude API response to OpenAI API format
    
    Args:
        claude_response: Response from Claude API
        
    Returns:
        OpenAI-formatted response
    """
    # Claude response is already converted to OpenAI format in the claude_completion function
    return claude_response
