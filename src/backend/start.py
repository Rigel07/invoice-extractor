#!/usr/bin/env python3
"""
Production-ready startup script for Invoice Extractor API
"""
import sys
import os
import logging
from pathlib import Path

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn
from main import app

def setup_logging():
    """Configure logging for production"""
    log_level = os.getenv("LOG_LEVEL", "info").upper()
    logging.basicConfig(
        level=getattr(logging, log_level),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

def validate_environment():
    """Validate required environment variables"""
    required_vars = ["GOOGLE_API_KEY"]
    missing_vars = []
    
    for var in required_vars:
        if not os.getenv(var):
            missing_vars.append(var)
    
    if missing_vars:
        print(f"ERROR: Missing required environment variables: {', '.join(missing_vars)}")
        print("Please set these variables in your .env file or environment.")
        sys.exit(1)
    
    print("✅ Environment validation passed - Google Gemini API configured")

if __name__ == "__main__":
    setup_logging()
    validate_environment()
    
    # Configuration
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 8000))
    workers = int(os.getenv("WORKERS", 1))
    log_level = os.getenv("LOG_LEVEL", "info").lower()
    
    print(f"Starting Invoice Extractor API on {host}:{port}")
    print(f"Workers: {workers}, Log Level: {log_level}")
    
    # For production, use gunicorn instead of uvicorn directly
    if os.getenv("ENVIRONMENT") == "production":
        print("Production mode: Use gunicorn for deployment")
        print("Command: gunicorn -w {workers} -k uvicorn.workers.UvicornWorker -b {host}:{port} main:app")
    else:
        # Development/testing mode
        uvicorn.run(
            app, 
            host=host, 
            port=port,
            log_level=log_level,
            reload=os.getenv("ENVIRONMENT") == "development"
        )
