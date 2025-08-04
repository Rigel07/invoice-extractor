#!/bin/bash
"""
Gunicorn configuration for production deployment
"""

# Server socket
bind = "0.0.0.0:8000"
backlog = 2048

# Worker processes
workers = 4
worker_class = "uvicorn.workers.UvicornWorker"
worker_connections = 1000
timeout = 60
keepalive = 2
max_requests = 1000
max_requests_jitter = 50

# Logging
loglevel = "info"
accesslog = "-"
errorlog = "-"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Process naming
proc_name = "invoice-extractor-api"

# Server mechanics
daemon = False
pidfile = "/tmp/invoice-extractor.pid"
user = None
group = None
tmp_upload_dir = None

# SSL (if needed)
# keyfile = "/path/to/key.pem"
# certfile = "/path/to/cert.pem"

# Performance
preload_app = True
worker_tmp_dir = "/dev/shm"  # Use RAM for better performance

# Restart workers periodically to prevent memory leaks
max_requests = 1000
max_requests_jitter = 100
