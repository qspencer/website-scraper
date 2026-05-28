#!/usr/bin/env bash
set -e

usage() {
    cat <<EOF
Usage: $(basename "$0") [--skip-tests] [--force-install] [-h|--help]

Bootstraps the venv, optionally runs the test suite as a launch gate, ensures the
Stirling-PDF container is up, kills any prior uvicorn started by this script, and
starts the app on http://127.0.0.1:8000.

Options:
  --skip-tests     Skip the pytest gate (useful for tight dev loops).
  --force-install  Run pip install even if requirements.txt is unchanged.
  -h, --help       Show this message.
EOF
}

SKIP_TESTS=0
FORCE_INSTALL=0
while [ $# -gt 0 ]; do
    case "$1" in
        --skip-tests)    SKIP_TESTS=1; shift ;;
        --force-install) FORCE_INSTALL=1; shift ;;
        -h|--help)       usage; exit 0 ;;
        *)               echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

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
STIRLING_MEMORY="4g"
# Cold start of the fat image can exceed 60s on first run — give it 180s before warning.
STIRLING_READY_ITERATIONS=90
STIRLING_READY_INTERVAL=2

cd "$PROJECT_DIR"

# Catch Ctrl-C / unexpected exit between steps so the user knows the script didn't finish cleanly.
# Any container or process this script may have started is left as-is; rerun the script to recover.
on_interrupt() {
    echo "" >&2
    echo "Interrupted. Script did not complete." >&2
    echo "If a Stirling-PDF container was just created, it is still running (docker ps)." >&2
    echo "Rerun $(basename "$0") to resume; PID file (if any) is left for next-run cleanup." >&2
    exit 130
}
trap on_interrupt INT TERM

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
        echo "WARNING: Docker not found — Stirling PDF will not be available (OCR and legacy .doc extraction disabled)."
        return
    fi

    if docker inspect "$STIRLING_NAME" &>/dev/null; then
        # If the existing container's image no longer matches $STIRLING_IMAGE (e.g. tag was
        # bumped in this file), recreate so the user gets the version they asked for.
        local CURRENT_IMAGE
        CURRENT_IMAGE=$(docker inspect -f '{{.Config.Image}}' "$STIRLING_NAME" 2>/dev/null || true)
        if [ -n "$CURRENT_IMAGE" ] && [ "$CURRENT_IMAGE" != "$STIRLING_IMAGE" ]; then
            echo "Stirling PDF image changed ($CURRENT_IMAGE -> $STIRLING_IMAGE); recreating container..."
            docker rm -f "$STIRLING_NAME" >/dev/null
        else
            # Apply --restart unless-stopped retroactively to containers created before this flag was added.
            local RESTART_POLICY
            RESTART_POLICY=$(docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' "$STIRLING_NAME" 2>/dev/null || true)
            if [ "$RESTART_POLICY" != "unless-stopped" ]; then
                docker update --restart unless-stopped "$STIRLING_NAME" >/dev/null
            fi
            # Apply memory limit retroactively if it has been bumped in this file since the
            # container was created (docker reports memory in bytes; convert STIRLING_MEMORY's
            # suffix-form for comparison). Skip silently if conversion isn't safe.
            local CURRENT_MEMORY DESIRED_MEMORY_BYTES
            CURRENT_MEMORY=$(docker inspect -f '{{.HostConfig.Memory}}' "$STIRLING_NAME" 2>/dev/null || echo 0)
            case "$STIRLING_MEMORY" in
                *g) DESIRED_MEMORY_BYTES=$(( ${STIRLING_MEMORY%g} * 1024 * 1024 * 1024 )) ;;
                *m) DESIRED_MEMORY_BYTES=$(( ${STIRLING_MEMORY%m} * 1024 * 1024 )) ;;
                *)  DESIRED_MEMORY_BYTES=0 ;;
            esac
            if [ "$DESIRED_MEMORY_BYTES" -gt 0 ] && [ "$CURRENT_MEMORY" != "$DESIRED_MEMORY_BYTES" ]; then
                echo "Updating Stirling PDF memory limit to $STIRLING_MEMORY..."
                docker update --memory "$STIRLING_MEMORY" --memory-swap "$STIRLING_MEMORY" "$STIRLING_NAME" >/dev/null
            fi
            if [ "$(docker inspect -f '{{.State.Running}}' "$STIRLING_NAME")" != "true" ]; then
                echo "Starting Stirling PDF container..."
                docker start "$STIRLING_NAME" >/dev/null
            else
                echo "Stirling PDF already running."
            fi
        fi
    fi

    if ! docker inspect "$STIRLING_NAME" &>/dev/null; then
        echo "Creating Stirling PDF container..."
        # --restart unless-stopped keeps the container up across host reboots; user can still
        # docker stop it explicitly when they want it down.
        # Guard against set -e: a failed create (port $STIRLING_PORT taken, image pull failure)
        # must not abort app launch — Stirling is optional, like the Docker-absent path above.
        if ! docker run -d \
            --name "$STIRLING_NAME" \
            --restart unless-stopped \
            --cpus "$STIRLING_CPUS" \
            --memory "$STIRLING_MEMORY" \
            --memory-swap "$STIRLING_MEMORY" \
            -p "$STIRLING_PORT:8080" \
            -e SECURITY_ENABLELOGIN=false \
            "$STIRLING_IMAGE"; then
            echo "WARNING: failed to start Stirling PDF (port $STIRLING_PORT in use, or image pull failed)." >&2
            echo "         OCR and legacy .doc extraction will be unavailable; the app will still start." >&2
            echo "         Inspect with: docker logs $STIRLING_NAME" >&2
            docker rm -f "$STIRLING_NAME" >/dev/null 2>&1 || true
            return
        fi
    fi

    if curl -sf "http://localhost:$STIRLING_PORT/api/v1/info/status" >/dev/null 2>&1; then
        echo "Stirling PDF is ready."
        return
    fi
    local READY_SECONDS=$((STIRLING_READY_ITERATIONS * STIRLING_READY_INTERVAL))
    echo -n "Waiting for Stirling PDF to be ready (up to ${READY_SECONDS}s)..."
    for i in $(seq 1 "$STIRLING_READY_ITERATIONS"); do
        if curl -sf "http://localhost:$STIRLING_PORT/api/v1/info/status" >/dev/null 2>&1; then
            echo " ready."
            return
        fi
        echo -n "."
        sleep "$STIRLING_READY_INTERVAL"
    done
    echo " WARNING: Stirling PDF did not become ready within ${READY_SECONDS}s (OCR and legacy .doc extraction may not work)."
}

# --- Create virtual environment if needed ---

if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

# --- Activate virtual environment ---

source "$VENV_DIR/bin/activate"

# --- Install dependencies ---
# Skip when requirements.txt hasn't been touched since the venv was created — common in the
# dev loop, and pip's no-op resolve still costs ~5s. Pass --force-install to bypass the skip.

VENV_MARKER="$VENV_DIR/pyvenv.cfg"
REQS_FILE="$PROJECT_DIR/requirements.txt"
if [ "$FORCE_INSTALL" -eq 0 ] && [ -f "$VENV_MARKER" ] && [ "$REQS_FILE" -ot "$VENV_MARKER" ]; then
    echo "Dependencies up to date (requirements.txt older than venv; use --force-install to override)."
else
    # Run pip in the background and emit a heartbeat dot every few seconds. A bare
    # `pip install -q` is silent for a minute+ on a cold venv and looks hung; the dots
    # (same idea as the Stirling wait below) prove progress while keeping output quiet.
    echo -n "Installing dependencies (first run can take a minute)"
    pip install -q -r "$REQS_FILE" &
    PIP_PID=$!
    while kill -0 "$PIP_PID" 2>/dev/null; do
        echo -n "."
        sleep 3
    done
    if wait "$PIP_PID"; then
        echo " done."
    else
        echo " failed — see pip output above." >&2
        exit 1
    fi
    touch "$VENV_MARKER"
fi

# --- Run tests (skip with --skip-tests) ---

if [ "$SKIP_TESTS" -eq 1 ]; then
    echo "Skipping tests (--skip-tests)."
else
    echo "Running tests..."
    if python -m pytest --tb=short -q; then
        echo ""
        echo "All tests passed."
    else
        echo ""
        echo "Tests failed. Fix the errors above (or rerun with --skip-tests to bypass)."
        exit 1
    fi
fi

# --- Ensure Stirling PDF is running ---

ensure_stirling_pdf

# --- Stop any previous instance ---

cleanup_old_process

# --- Start the app in the background ---

LOG_DIR="$(dirname "$LOG_FILE")"
mkdir -p "$LOG_DIR"

# Cap uvicorn.log: unlike scraper.log (RotatingFileHandler), this file is only
# shell-appended, so roll it to .1 once it passes 10MB to bound unbounded growth.
UVICORN_LOG_MAX_BYTES=$((10 * 1024 * 1024))
if [ -f "$LOG_FILE" ]; then
    SIZE=$(wc -c < "$LOG_FILE" 2>/dev/null || echo 0)
    if [ "$SIZE" -gt "$UVICORN_LOG_MAX_BYTES" ]; then
        mv -f "$LOG_FILE" "$LOG_FILE.1"
        echo "Rolled uvicorn.log ($SIZE bytes) to uvicorn.log.1"
    fi
fi

# Prune orphaned rotated scraper logs above the handler's backupCount (3). Older
# installs left scraper.log.4/.5 behind that the rotation will never reclaim.
for f in "$LOG_DIR"/scraper.log.[4-9] "$LOG_DIR"/scraper.log.[1-9][0-9]; do
    [ -f "$f" ] && rm -f "$f"
done

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
