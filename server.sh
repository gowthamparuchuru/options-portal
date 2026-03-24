#!/bin/bash
PIDFILE="server.pid"
LOGFILE="server.log"

start() {
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        echo "Server is already running (PID: $(cat "$PIDFILE"))"
        return
    fi
    echo "Building frontend..."
    (cd frontend && npm run build)
    if [ $? -ne 0 ]; then
        echo "Frontend build failed. Aborting."
        return 1
    fi
    echo "Starting server..."
    nohup python3 run.py > "$LOGFILE" 2>&1 &
    echo $! > "$PIDFILE"
    echo "Server started (PID: $!)"
}

stop() {
    if [ ! -f "$PIDFILE" ]; then
        echo "Server is not running (no PID file)"
        return
    fi
    PID=$(cat "$PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "Stopping server (PID: $PID)..."
        kill "$PID"
        rm -f "$PIDFILE"
        echo "Server stopped"
    else
        echo "Server is not running (stale PID file)"
        rm -f "$PIDFILE"
    fi
}

status() {
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        echo "Server is running (PID: $(cat "$PIDFILE"))"
    else
        echo "Server is not running"
    fi
}

logs() {
    tail -f "$LOGFILE"
}

case "$1" in
    start)  start ;;
    stop)   stop ;;
    restart) stop; sleep 1; start ;;
    status) status ;;
    logs)   logs ;;
    *)
        echo "Usage: ./server.sh {start|stop|restart|status|logs}"
        exit 1
        ;;
esac
