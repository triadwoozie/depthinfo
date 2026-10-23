#!/usr/bin/env bash
# Start the Sonar Imaging backend (port 8000) and dashboard (port 3000). Ctrl+C stops both.
#
#   ./run.sh                    real hardware mode, production dashboard build
#   ./run.sh --mock             synthetic audio and video, no hardware
#   ./run.sh --mock --real-camera   synthetic sonar with the real camera
#   ./run.sh --dev              dashboard with hot reload
#   ./run.sh --setup            first run: create the Python venv and install npm packages
#
# Other flags: --model (download depth model), --rebuild, --backend-only, --open
# Logs go to logs/backend.log and logs/dashboard.log. Linux and macOS.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
DASHBOARD="$ROOT/dashboard"
LOGS="$ROOT/logs"
GRACE_S=5

MOCK=0 REAL_CAMERA=0 MODEL=0 DEV=0 REBUILD=0 SETUP=0 BACKEND_ONLY=0 OPEN=0
for arg in "$@"; do
  case "$arg" in
    --mock) MOCK=1 ;;
    --real-camera) REAL_CAMERA=1 ;;
    --model) MODEL=1 ;;
    --dev) DEV=1 ;;
    --rebuild) REBUILD=1 ;;
    --setup) SETUP=1 ;;
    --backend-only) BACKEND_ONLY=1 ;;
    --open) OPEN=1 ;;
    -h|--help) sed -n '2,11p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "[run] unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

say() { printf '[run] %s\n' "$*"; }
PIDS=() NAMES=()
STOPPING=0

stop_all() {
  [ "$STOPPING" = 1 ] && return
  STOPPING=1
  local live=() i
  for i in "${!PIDS[@]}"; do kill -0 "${PIDS[$i]}" 2>/dev/null && live+=("$i"); done
  [ "${#live[@]}" -gt 0 ] || return 0
  local names=""
  for i in "${live[@]}"; do names+="${NAMES[$i]} "; done
  say "stopping ${names}..."
  # Each child leads its own process group (setsid), so signal the whole group.
  for i in "${live[@]}"; do kill -TERM -- "-${PIDS[$i]}" 2>/dev/null; done
  local waited=0
  while [ "$waited" -lt $((GRACE_S * 10)) ]; do
    local any=0
    for i in "${live[@]}"; do kill -0 "${PIDS[$i]}" 2>/dev/null && any=1; done
    [ "$any" = 0 ] && break
    sleep 0.1; waited=$((waited + 1))
  done
  for i in "${live[@]}"; do
    if kill -0 "${PIDS[$i]}" 2>/dev/null; then
      say "${NAMES[$i]} ignored SIGTERM; killing it"
      kill -KILL -- "-${PIDS[$i]}" 2>/dev/null
    fi
  done
  say "stopped."
}

die() { echo "[run] ERROR: $*" >&2; stop_all; exit 1; }
trap 'stop_all; exit 130' INT
trap 'stop_all; exit 143' TERM
trap 'stop_all' EXIT

# --- environment -------------------------------------------------------------
PY="$BACKEND/.venv/bin/python"
NODE_BIN=""
if command -v npm >/dev/null 2>&1; then NODE_BIN="$(dirname "$(command -v npm)")"
elif [ -n "${NODE_BIN_DIR:-}" ] && [ -x "$NODE_BIN_DIR/npm" ]; then NODE_BIN="$NODE_BIN_DIR"
elif [ -x "$HOME/.local/node/bin/npm" ]; then NODE_BIN="$HOME/.local/node/bin"; fi
[ -n "$NODE_BIN" ] && export PATH="$NODE_BIN:$PATH"
[ "$REAL_CAMERA" = 1 ] && export SONAR_REAL_CAMERA=true
[ "$MODEL" = 1 ] && export DEPTH_AUTO_DOWNLOAD=true

if [ ! -x "$PY" ]; then
  [ "$SETUP" = 1 ] || die "backend/.venv not found. Run './run.sh --setup' once to install everything."
  if command -v uv >/dev/null 2>&1; then
    (cd "$BACKEND" && uv venv --python 3.12 .venv && uv pip install --python "$PY" -r requirements.txt) || die "backend setup failed"
  else
    PYX=""; for c in python3.12 python3.11; do command -v "$c" >/dev/null 2>&1 && PYX="$c" && break; done
    [ -n "$PYX" ] || die "Python 3.11 or 3.12 is required (3.13+ has no wheels for the pinned packages). Install it, or install uv."
    (cd "$BACKEND" && "$PYX" -m venv .venv && "$PY" -m pip install -r requirements.txt) || die "backend setup failed"
  fi
fi

if [ "$BACKEND_ONLY" = 0 ]; then
  [ -n "$NODE_BIN" ] || die "npm not found. Install Node.js 18+ (or set NODE_BIN_DIR)."
  if [ ! -d "$DASHBOARD/node_modules" ]; then
    [ "$SETUP" = 1 ] || die "dashboard/node_modules not found. Run './run.sh --setup' once."
    (cd "$DASHBOARD" && npm ci) || die "npm ci failed"
  fi
  STALE=0
  if [ ! -f "$DASHBOARD/.next/BUILD_ID" ]; then STALE=1
  elif [ -n "$(find "$DASHBOARD/src" "$DASHBOARD/package.json" "$DASHBOARD/tailwind.config.ts" "$DASHBOARD/next.config.mjs" "$DASHBOARD/postcss.config.mjs" -type f -newer "$DASHBOARD/.next/BUILD_ID" -print -quit 2>/dev/null)" ]; then STALE=1; fi
  if [ "$DEV" = 0 ] && { [ "$REBUILD" = 1 ] || [ "$STALE" = 1 ]; }; then
    say "dashboard build is missing or older than its sources; rebuilding ..."
    (cd "$DASHBOARD" && npm run build) || die "dashboard build failed"
  fi
fi

port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
port_busy 8000 && die "port 8000 is already in use (is the backend already running?). Find it with: ss -ltnp | grep :8000"
[ "$BACKEND_ONLY" = 0 ] && port_busy 3000 && die "port 3000 is already in use (is the dashboard already running?). Find it with: ss -ltnp | grep :3000"

# --- start -------------------------------------------------------------------
mkdir -p "$LOGS"

start() { # name dir cmd...
  local name="$1" dir="$2"; shift 2
  (cd "$dir" && exec setsid "$@" >"$LOGS/$name.log" 2>&1) &
  PIDS+=("$!"); NAMES+=("$name")
}

wait_http() { # name url pid
  local name="$1" url="$2" pid="$3" i
  for i in $(seq 1 225); do
    kill -0 "$pid" 2>/dev/null || die "$name exited during start-up:"$'\n'"$(tail -n 15 "$LOGS/$name.log")"
    if command -v curl >/dev/null 2>&1; then curl -fs -o /dev/null --max-time 1.5 "$url" && return 0
    else "$PY" -c "import urllib.request,sys; urllib.request.urlopen('$url', timeout=1.5)" 2>/dev/null && return 0; fi
    sleep 0.4
  done
  die "$name did not answer on $url within 90s:"$'\n'"$(tail -n 15 "$LOGS/$name.log")"
}

BACKEND_ARGS=(main.py); [ "$MOCK" = 1 ] && BACKEND_ARGS+=(--mock)
say "starting backend ($([ "$MOCK" = 1 ] && echo mock || echo real hardware) mode) ..."
start backend "$BACKEND" "$PY" "${BACKEND_ARGS[@]}"
wait_http backend "http://localhost:8000/healthz" "${PIDS[0]}"

if [ "$BACKEND_ONLY" = 0 ]; then
  say "starting dashboard ($([ "$DEV" = 1 ] && echo dev || echo production)) ..."
  start dashboard "$DASHBOARD" npm run "$([ "$DEV" = 1 ] && echo dev || echo start)"
  wait_http dashboard "http://localhost:3000" "${PIDS[1]}"
fi

echo
say "running. Press Ctrl+C to stop everything."
[ "$BACKEND_ONLY" = 0 ] && say "  dashboard : http://localhost:3000"
say "  backend   : http://localhost:8000/healthz   (API docs: http://localhost:8000/docs)"
say "  logs      : $LOGS/backend.log, dashboard.log"
if [ "$OPEN" = 1 ] && [ "$BACKEND_ONLY" = 0 ]; then
  (xdg-open http://localhost:3000 || open http://localhost:3000) >/dev/null 2>&1 &
fi

while true; do
  for i in "${!PIDS[@]}"; do
    kill -0 "${PIDS[$i]}" 2>/dev/null || die "${NAMES[$i]} stopped unexpectedly:"$'\n'"$(tail -n 15 "$LOGS/${NAMES[$i]}.log")"
  done
  sleep 0.5
done
