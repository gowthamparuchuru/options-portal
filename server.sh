#!/bin/bash

activate_venv() {
    source venv/bin/activate
}

build() {
    activate_venv
    echo "Installing backend dependencies..."
    pip install -r requirements.txt
    echo "Installing frontend dependencies..."
    (cd frontend && npm install)
    echo "Building frontend..."
    (cd frontend && npm run build)
}

start() {
    activate_venv
    python3 run.py
}

case "$1" in
    build) build ;;
    start) start ;;
    *)
        echo "Usage: ./server.sh {build|start}"
        exit 1
        ;;
esac
