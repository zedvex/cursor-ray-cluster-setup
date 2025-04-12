#!/usr/bin/env python3
"""
FastAPI server that provides an OpenAI-compatible API endpoint 
for Claude API, distributed via Ray cluster.
"""

import os
import sys
import json
import logging
import time
from typing import Dict, List, Any, Optional
from dotenv import load_dotenv
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
import ray

# Add the project root to the Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, project_root)

from ray_tasks.gpt_proxy import claude_completion, openai_to_claude_prompt
from ray_tasks.resource_utils import get_cluster_resources

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Check for API key
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
if not ANTHROPIC_API_KEY:
    logger.warning("ANTHROPIC_API_KEY not found in environment variables. "
                   "You will need to provide it in the API requests.")

# Configure server
API_SERVER_PORT = int(os.getenv("API_SERVER_PORT", "8000"))
API_SERVER_HOST = os.getenv("API_SERVER_HOST", "0.0.0.0")

# Initialize FastAPI
app = FastAPI(
    title="Ray Claude Proxy",
    description="OpenAI-compatible API proxy for Claude AI using Ray for distribution",
    version="1.0.0"
)

# Initialize Ray when the server starts
@app.on_event("startup")
async def startup_event():
    logger.info("Starting Ray Claude Proxy Server")
    
    try:
        ray.init(address="auto", ignore_reinit_error=True)
        logger.info("Connected to existing Ray cluster")
        
        # Print cluster resources
        resources = get_cluster_resources()
        logger.info(f"Cluster resources: {resources['total_nodes']} nodes, "
                    f"{resources['total_cpus']} CPUs, "
                    f"{resources['total_gpus']} GPUs, "
                    f"{resources['total_memory_gb']:.1f} GB memory")
                    
    except ConnectionError:
        logger.warning("Could not connect to Ray cluster, initializing local Ray instance")
        ray.init(ignore_reinit_error=True)

# Shutdown Ray when the server stops
@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down Ray Claude Proxy Server")
    ray.shutdown()

# Health check endpoint
@app.get("/health")
async def health_check():
    # Check if Ray is running
    if not ray.is_initialized():
        raise HTTPException(status_code=503, detail="Ray cluster not available")
    
    # Get cluster resources
    resources = get_cluster_resources()
    
    return {
        "status": "healthy",
        "ray_status": "connected",
        "cluster": {
            "nodes": resources["total_nodes"],
            "available_cpus": resources["available_cpus"],
            "total_cpus": resources["total_cpus"],
            "available_memory_gb": resources["available_memory_gb"],
            "total_memory_gb": resources["total_memory_gb"],
        },
        "timestamp": time.time()
    }

# OpenAI-compatible API endpoint for chat completions
@app.post("/v1/chat/completions")
async def openai_chat_completions(request: Request):
    try:
        # Get request body
        body = await request.json()
        
        # Extract API key from request headers
        api_key = request.headers.get("Authorization", "").replace("Bearer ", "")
        if not api_key:
            api_key = ANTHROPIC_API_KEY
        
        if not api_key:
            raise HTTPException(
                status_code=401, 
                detail="API key is required. Provide it in the Authorization header."
            )
        
        # Convert OpenAI format to Claude format
        claude_params = openai_to_claude_prompt(body)
        
        # Add API key to params
        claude_params["api_key"] = api_key
        
        # Call Claude API via Ray
        start_time = time.time()
        
        # Submit task
        result_ref = claude_completion.remote(**claude_params)
        
        # Get result
        result = ray.get(result_ref)
        
        elapsed_time = time.time() - start_time
        logger.info(f"Request processed in {elapsed_time:.2f}s")
        
        # Return response in OpenAI format
        return result
        
    except Exception as e:
        logger.error(f"Error processing request: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error processing request: {str(e)}")

# Legacy OpenAI completions endpoint for compatibility
@app.post("/v1/completions")
async def openai_completions(request: Request):
    try:
        # Get request body
        body = await request.json()
        
        # Extract API key from request headers
        api_key = request.headers.get("Authorization", "").replace("Bearer ", "")
        if not api_key:
            api_key = ANTHROPIC_API_KEY
        
        if not api_key:
            raise HTTPException(
                status_code=401, 
                detail="API key is required. Provide it in the Authorization header."
            )
        
        # Convert prompt to messages format
        if "prompt" in body:
            prompt = body["prompt"]
            if isinstance(prompt, list):
                prompt = prompt[0]  # Take the first prompt
            
            # Create messages format
            body["messages"] = [{"role": "user", "content": prompt}]
        
        # Convert OpenAI format to Claude format
        claude_params = openai_to_claude_prompt(body)
        
        # Add API key to params
        claude_params["api_key"] = api_key
        
        # Call Claude API via Ray
        start_time = time.time()
        
        # Submit task
        result_ref = claude_completion.remote(**claude_params)
        
        # Get result
        result = ray.get(result_ref)
        
        elapsed_time = time.time() - start_time
        logger.info(f"Completion request processed in {elapsed_time:.2f}s")
        
        # For completions (not chat), reformat the response
        if "choices" in result:
            for choice in result["choices"]:
                if "message" in choice and "content" in choice["message"]:
                    choice["text"] = choice["message"]["content"]
        
        # Return response
        return result
        
    except Exception as e:
        logger.error(f"Error processing completion request: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error processing request: {str(e)}")

# Info endpoints for developer use
@app.get("/info/models")
async def get_models():
    # Provide a compatibility layer for applications expecting OpenAI's models endpoint
    return {
        "data": [
            {
                "id": "claude-3-7-sonnet-20250219",
                "object": "model",
                "created": 1677610602,
                "owned_by": "anthropic"
            },
            {
                "id": "claude-3-opus-20240229",
                "object": "model",
                "created": 1677610602,
                "owned_by": "anthropic"
            },
            {
                "id": "claude-3-5-sonnet-20240620",
                "object": "model",
                "created": 1677610602,
                "owned_by": "anthropic"
            }
        ],
        "object": "list"
    }

# Main entry point
def main():
    try:
        logger.info(f"Starting server on http://{API_SERVER_HOST}:{API_SERVER_PORT}")
        uvicorn.run(
            "scripts.start_ray_proxy:app", 
            host=API_SERVER_HOST,
            port=API_SERVER_PORT,
            reload=False
        )
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error(f"Error starting server: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
