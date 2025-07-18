#!/usr/bin/env python3
"""
Production startup script for Invoice Extractor API
"""
import uvicorn
import os
from dotenv import load_dotenv

if __name__ == "__main__":
    load_dotenv()
    
    # Production configuration
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        workers=int(os.getenv("WORKERS", 1)),
        reload=False,
        log_level=os.getenv("LOG_LEVEL", "info")
    )
