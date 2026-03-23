#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="$PROJECT_DIR/venv"
PID_FILE="$PROJECT_DIR/.uvicorn.pid"
LOG_FILE="$PROJECT_DIR/logs/uvicorn.log"
HOST="127.0.0.1"
PORT=8000

cd "$PROJECT_DIR"

# --- Helper functions ---

cleanup_old_process() {
    if [ -f "$PID_FILE" ]; then
        OLD_PID=$(cat "$PID_FILE")
        if kill -0 "$OLD_PID" 2>/dev/null; then
            echo "Stopping previous instance (PID $OLD_PID)..."
            kill "$OLD_PID" 2>/dev/null || true
            sleep 1
        fi
        rm -f "$PID_FILE"
    fi
}

# --- Create virtual environment if needed ---

if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

# --- Activate virtual environment ---

source "$VENV_DIR/bin/activate"

# --- Install dependencies ---

echo "Installing dependencies..."
pip install -q -r requirements.txt

# --- Run tests ---

echo "Running tests..."
if python -m pytest --tb=short -q; then
    echo ""
    echo "All tests passed."
else
    echo ""
    echo "Tests failed. Fix the errors above before running the app."
    exit 1
fi

# --- Stop any previous instance ---

cleanup_old_process

# --- Start the app in the background ---

mkdir -p "$(dirname "$LOG_FILE")"

echo "Starting app..."
nohup uvicorn app.main:app --host "$HOST" --port "$PORT" > "$LOG_FILE" 2>&1 &
APP_PID=$!
echo "$APP_PID" > "$PID_FILE"

# Wait briefly and verify it started
sleep 2
if kill -0 "$APP_PID" 2>/dev/null; then
    echo ""
    echo "========================================="
    echo "  App is running (PID $APP_PID)"
    echo "  URL: http://$HOST:$PORT/"
    echo "  Log: $LOG_FILE"
    echo "  Stop: kill $APP_PID"
    echo "========================================="
else
    echo "App failed to start. Check $LOG_FILE for details."
    exit 1
fi
