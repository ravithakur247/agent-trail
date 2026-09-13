"""Command-line entry point for AgentTrail: start, stop, dashboard."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

import psutil
import uvicorn

PID_FILE = Path.home() / ".agenttrail" / "daemon.pid"
LOG_FILE = Path.home() / ".agenttrail" / "daemon.log"
DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = 8420


def _read_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text().strip())
    except ValueError:
        return None


def _write_pid(pid: int) -> None:
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(pid))


def cmd_start(args: argparse.Namespace) -> None:
    existing_pid = _read_pid()
    if existing_pid and psutil.pid_exists(existing_pid):
        print(f"AgentTrail daemon already running (pid {existing_pid})")
        return

    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    log = open(LOG_FILE, "a", buffering=1)
    proc = subprocess.Popen(
        [sys.executable, "-m", "agenttrail.daemon"],
        stdout=log,
        stderr=log,
        **kwargs,
    )
    _write_pid(proc.pid)
    print(f"AgentTrail daemon started (pid {proc.pid})")
    print(f"Logs: {LOG_FILE}")


def cmd_stop(args: argparse.Namespace) -> None:
    pid = _read_pid()
    if pid is None or not psutil.pid_exists(pid):
        print("AgentTrail daemon is not running")
        PID_FILE.unlink(missing_ok=True)
        return

    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass

    for _ in range(20):
        if not psutil.pid_exists(pid):
            break
        time.sleep(0.25)

    PID_FILE.unlink(missing_ok=True)
    print("AgentTrail daemon stopped")


def cmd_dashboard(args: argparse.Namespace) -> None:
    from . import api  # imported lazily so `agenttrail stop` etc. stay fast to start

    url = f"http://{DASHBOARD_HOST}:{DASHBOARD_PORT}"
    print(f"AgentTrail dashboard running at {url}")
    webbrowser.open(url)
    uvicorn.run(api.app, host=DASHBOARD_HOST, port=DASHBOARD_PORT, log_level="warning")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agenttrail", description="Monitor AI coding agents on your machine."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start", help="Start the background monitoring daemon")
    start_parser.set_defaults(func=cmd_start)

    stop_parser = subparsers.add_parser("stop", help="Stop the background monitoring daemon")
    stop_parser.set_defaults(func=cmd_stop)

    dashboard_parser = subparsers.add_parser("dashboard", help="Open the web dashboard")
    dashboard_parser.set_defaults(func=cmd_dashboard)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
