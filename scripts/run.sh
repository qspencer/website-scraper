#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="$PROJECT_DIR/venv"
PID_FILE="$PROJECT_DIR/.uvicorn.pid"
LOG_FILE="$PROJECT_DIR/logs/uvicorn.log"
HOST="127.0.0.1"
PORT=8000

STIRLING_IMAGE="ghcr.io/stirling-tools/stirling-pdf:2.7.2-fat"
STIRLING_NAME="stirling-pdf"
STIRLING_PORT=8080
STIRLING_CPUS=2
STIRLING_MEMORY="2g"

cd "$PROJECT_DIR"

# --- Helper functions ---

cleanup_old_process() {
    local OLD_PID=""
    if [ -f "$PID_FILE" ]; then
        OLD_PID=$(cat "$PID_FILE")
    fi

    # Kill our own previous instance by PID
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Stopping previous instance (PID $OLD_PID)..."
        kill "$OLD_PID" 2>/dev/null || true
        sleep 2
        if kill -0 "$OLD_PID" 2>/dev/null; then
            echo "Force killing PID $OLD_PID..."
            kill -9 "$OLD_PID" 2>/dev/null || true
            sleep 1
        fi
    fi
    rm -f "$PID_FILE"

    # Anything still holding the port — only kill if it's our PID, otherwise refuse.
    # Avoids force-killing an unrelated process that happens to be on port $PORT.
    local PORT_PIDS
    PORT_PIDS=$(lsof -ti :"$PORT" 2>/dev/null || true)
    if [ -n "$PORT_PIDS" ]; then
        for pid in $PORT_PIDS; do
            if [ -n "$OLD_PID" ] && [ "$pid" = "$OLD_PID" ]; then
                continue  # already handled above
            fi
            echo "ERROR: port $PORT is held by an unrelated process (PID $pid)." >&2
            echo "       Refusing to kill it. Free the port manually and retry:" >&2
            echo "       lsof -i :$PORT     # to inspect" >&2
            echo "       kill $pid          # if you're sure" >&2
            exit 1
        done
    fi
}

ensure_stirling_pdf() {
    if ! command -v docker &>/dev/null; then
        echo "WARNING: Docker not found — Stirling PDF (OCR) will not be available."
        return
    fi

    # Check if container exists
    if docker inspect "$STIRLING_NAME" &>/dev/null; then
        # Container exists — start it if stopped
        if [ "$(docker inspect -f '{{.State.Running}}' "$STIRLING_NAME")" != "true" ]; then
            echo "Starting Stirling PDF container..."
            docker start "$STIRLING_NAME" >/dev/null
        else
            echo "Stirling PDF already running."
        fi
    else
        # Container doesn't exist — create and start it
        echo "Creating Stirling PDF container..."
        docker run -d \
            --name "$STIRLING_NAME" \
            --cpus "$STIRLING_CPUS" \
            --memory "$STIRLING_MEMORY" \
            --memory-swap "$STIRLING_MEMORY" \
            -p "$STIRLING_PORT:8080" \
            -e SECURITY_ENABLELOGIN=false \
            "$STIRLING_IMAGE"
    fi

    # Wait for Stirling to be ready
    if curl -sf "http://localhost:$STIRLING_PORT/api/v1/info/status" >/dev/null 2>&1; then
        echo "Stirling PDF is ready."
        return
    fi
    echo -n "Waiting for Stirling PDF to be ready..."
    for i in $(seq 1 30); do
        if curl -sf "http://localhost:$STIRLING_PORT/api/v1/info/status" >/dev/null 2>&1; then
            echo " ready."
            return
        fi
        echo -n "."
        sleep 2
    done
    echo " WARNING: Stirling PDF did not become ready within 60s (OCR may not work)."
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

# --- Ensure Stirling PDF is running ---

ensure_stirling_pdf

# --- Stop any previous instance ---

cleanup_old_process

# --- Start the app in the background ---

mkdir -p "$(dirname "$LOG_FILE")"

echo "Starting app..."
# Append (>>) rather than truncate (>) so prior session's startup output and any crash trace survive.
echo "" >> "$LOG_FILE"
echo "===== $(date -Iseconds) ===== app start =====" >> "$LOG_FILE"
nohup uvicorn app.main:app --host "$HOST" --port "$PORT" >> "$LOG_FILE" 2>&1 &
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
    echo "  Stirling PDF: http://localhost:$STIRLING_PORT/"
    echo "  Stop: kill $APP_PID"
    echo "========================================="
else
    echo "App failed to start. Check $LOG_FILE for details."
    exit 1
fi
