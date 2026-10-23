#!/usr/bin/env python3
"""Start the Sonar Imaging backend (port 8000) and dashboard (port 3000). Ctrl+C stops both.

    ./run.py                  # real hardware mode, production dashboard build
    ./run.py --mock           # synthetic audio and video, no hardware
    ./run.py --mock --real-camera   # synthetic sonar with the real camera
    ./run.py --dev            # dashboard with hot reload
    ./run.py --setup          # first run: create the Python venv and install npm packages

Logs go to logs/backend.log and logs/dashboard.log. Linux and macOS.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND, DASHBOARD, LOGS = ROOT / "backend", ROOT / "dashboard", ROOT / "logs"
BACKEND_URL, DASHBOARD_URL = "http://localhost:8000", "http://localhost:3000"
GRACE_S = 5.0

children: list[tuple[str, subprocess.Popen]] = []
stopping = False


def say(msg: str) -> None:
    print(f"[run] {msg}", flush=True)


def die(msg: str) -> None:
    print(f"[run] ERROR: {msg}", file=sys.stderr, flush=True)
    shutdown()
    sys.exit(1)


# ---------------------------------------------------------------------------
# Environment discovery and setup
# ---------------------------------------------------------------------------

def venv_python() -> Path:
    return BACKEND / ".venv" / "bin" / "python"


def find_node_bin() -> str | None:
    """Directory holding npm: $NODE_BIN_DIR, PATH, then a user-local install (~/.local/node/bin)."""
    candidates = [os.environ.get("NODE_BIN_DIR", ""), str(Path.home() / ".local/node/bin")]
    on_path = shutil.which("npm")
    if on_path:
        return str(Path(on_path).parent)
    return next((c for c in candidates if c and (Path(c) / "npm").exists()), None)


def run_checked(cmd: list[str], cwd: Path, env: dict[str, str]) -> None:
    say("$ " + " ".join(cmd))
    if subprocess.run(cmd, cwd=cwd, env=env).returncode != 0:
        die(f"command failed: {' '.join(cmd)}")


def setup_backend() -> None:
    req = BACKEND / "requirements.txt"
    if shutil.which("uv"):
        run_checked(["uv", "venv", "--python", "3.12", ".venv"], BACKEND, dict(os.environ))
        run_checked(["uv", "pip", "install", "--python", str(venv_python()), "-r", str(req)],
                    BACKEND, dict(os.environ))
        return
    for exe in ("python3.12", "python3.11"):
        if shutil.which(exe):
            run_checked([exe, "-m", "venv", ".venv"], BACKEND, dict(os.environ))
            run_checked([str(venv_python()), "-m", "pip", "install", "-r", str(req)], BACKEND, dict(os.environ))
            return
    die("Python 3.11 or 3.12 is required (3.13+ has no wheels for the pinned packages). "
        "Install it, or install uv (pip install --user uv), then re-run with --setup.")


def build_is_stale() -> bool:
    """True if there is no production build, or any dashboard source is newer than it."""
    marker = DASHBOARD / ".next" / "BUILD_ID"
    if not marker.exists():
        return True
    built = marker.stat().st_mtime
    sources = [DASHBOARD / n for n in ("package.json", "tailwind.config.ts", "next.config.mjs", "postcss.config.mjs")]
    sources += [p for p in (DASHBOARD / "src").rglob("*") if p.is_file()]
    return any(p.exists() and p.stat().st_mtime > built for p in sources)


def ensure_ready(args: argparse.Namespace, env: dict[str, str]) -> None:
    if not venv_python().exists():
        if not args.setup:
            die("backend/.venv not found. Run `./run.py --setup` once to install everything.")
        setup_backend()
    if args.backend_only:
        return
    if not find_node_bin():
        die("npm not found. Install Node.js 18+ (or set NODE_BIN_DIR).")
    if not (DASHBOARD / "node_modules").exists():
        if not args.setup:
            die("dashboard/node_modules not found. Run `./run.py --setup` once.")
        run_checked(["npm", "ci"], DASHBOARD, env)
    if not args.dev and (args.rebuild or build_is_stale()):
        say("dashboard build is missing or older than its sources; rebuilding ...")
        run_checked(["npm", "run", "build"], DASHBOARD, env)


# ---------------------------------------------------------------------------
# Process management
# ---------------------------------------------------------------------------

def port_busy(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def spawn(name: str, cmd: list[str], cwd: Path, env: dict[str, str]) -> subprocess.Popen:
    LOGS.mkdir(exist_ok=True)
    log = open(LOGS / f"{name}.log", "w")
    # Own session/process group so the whole tree (npm -> next, python threads) can be signalled.
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
                            start_new_session=True)
    children.append((name, proc))
    return proc


def tail(name: str, lines: int = 15) -> str:
    try:
        return "".join((LOGS / f"{name}.log").read_text(errors="replace").splitlines(True)[-lines:])
    except OSError:
        return ""


def wait_http(name: str, url: str, proc: subprocess.Popen, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            die(f"{name} exited with code {proc.returncode} during start-up:\n{tail(name)}")
        try:
            with urllib.request.urlopen(url, timeout=1.5) as resp:
                if resp.status < 500:
                    return
        except Exception:
            time.sleep(0.4)
    die(f"{name} did not answer on {url} within {timeout:.0f}s:\n{tail(name)}")


def kill_group(proc: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def shutdown() -> None:
    """SIGTERM every child group, wait a grace period, then SIGKILL what is left."""
    global stopping
    if stopping:
        return
    stopping = True
    live = [(n, p) for n, p in children if p.poll() is None]
    if live:
        say("stopping " + ", ".join(n for n, _ in live) + " ...")
    for _, p in live:
        kill_group(p, signal.SIGTERM)
    deadline = time.monotonic() + GRACE_S
    while time.monotonic() < deadline and any(p.poll() is None for _, p in live):
        time.sleep(0.1)
    for name, p in live:
        if p.poll() is None:
            say(f"{name} ignored SIGTERM; killing it")
            kill_group(p, signal.SIGKILL)
            p.wait()
    if live:
        say("stopped.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mock", action="store_true", help="synthetic audio and video (no hardware)")
    ap.add_argument("--real-camera", action="store_true", help="use the real camera even with --mock")
    ap.add_argument("--model", action="store_true", help="download the depth model if missing (99 MB)")
    ap.add_argument("--dev", action="store_true", help="dashboard dev server (hot reload)")
    ap.add_argument("--rebuild", action="store_true", help="force a production dashboard rebuild")
    ap.add_argument("--setup", action="store_true", help="create the venv / install npm packages if missing")
    ap.add_argument("--backend-only", action="store_true", help="do not start the dashboard")
    ap.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    args = ap.parse_args()

    signal.signal(signal.SIGINT, lambda *_: (shutdown(), sys.exit(130)))
    signal.signal(signal.SIGTERM, lambda *_: (shutdown(), sys.exit(143)))

    env = dict(os.environ)
    node_bin = find_node_bin()
    if node_bin:
        env["PATH"] = node_bin + os.pathsep + env.get("PATH", "")
    if args.real_camera:
        env["SONAR_REAL_CAMERA"] = "true"
    if args.model:
        env["DEPTH_AUTO_DOWNLOAD"] = "true"

    ensure_ready(args, env)
    for port, what in ((8000, "backend"), (3000, "dashboard")):
        if (what == "backend" or not args.backend_only) and port_busy(port):
            die(f"port {port} is already in use (is {what} already running?). "
                f"Find it with: ss -ltnp | grep :{port}")

    backend_cmd = [str(venv_python()), "main.py"] + (["--mock"] if args.mock else [])
    say(f"starting backend ({'mock' if args.mock else 'real hardware'} mode) ...")
    backend = spawn("backend", backend_cmd, BACKEND, env)
    wait_http("backend", BACKEND_URL + "/healthz", backend, 90)

    if not args.backend_only:
        say(f"starting dashboard ({'dev' if args.dev else 'production'}) ...")
        dash = spawn("dashboard", ["npm", "run", "dev" if args.dev else "start"], DASHBOARD, env)
        wait_http("dashboard", DASHBOARD_URL, dash, 90)

    print()
    say("running. Press Ctrl+C to stop everything.")
    if not args.backend_only:
        say(f"  dashboard : {DASHBOARD_URL}")
    say(f"  backend   : {BACKEND_URL}/healthz   (API docs: {BACKEND_URL}/docs)")
    say(f"  logs      : {LOGS}/backend.log, dashboard.log")
    if args.open and not args.backend_only:
        webbrowser.open(DASHBOARD_URL)

    try:
        while True:
            for name, proc in children:
                if proc.poll() is not None:
                    die(f"{name} stopped unexpectedly (code {proc.returncode}):\n{tail(name)}")
            time.sleep(0.5)
    finally:
        shutdown()


if __name__ == "__main__":
    main()
