#!/bin/bash

# Start script for Railway deployments

if [ "$SERVICE_TYPE" = "worker" ]; then
    echo "Starting Background Worker..."
    exec python -m worker.refresh_worker
elif [ "$SERVICE_TYPE" = "scheduler" ]; then
    echo "Starting Scheduler..."
    exec python -m worker.scheduler
else
    echo "Starting FastAPI Web Server..."
    exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
fi
