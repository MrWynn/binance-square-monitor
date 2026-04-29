"""Start/stop all monitor processes from one command.

Usage:
    python manage_processes.py start
    python manage_processes.py stop
    python manage_processes.py restart
    python manage_processes.py status
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from log_utils import setup_file_logging


setup_file_logging()

BASE_DIR = Path(__file__).resolve().parent
PID_FILE = BASE_DIR / ".monitor_processes.json"
WEB_URL = "http://127.0.0.1:8000"
STOP_GRACE_SECONDS = 12
STOP_KILL_SECONDS = 4
PROGRAMS = [
    ("worker", "worker.py"),
    ("market_realtime", "market_realtime.py"),
    ("web", "web.py"),
    ("auto_trader", "auto_trader.py"),
]


def _load_state() -> dict:
    if not PID_FILE.exists():
        return {}
    try:
        return json.loads(PID_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict):
    PID_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _pid_running(pid: int | None) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
        stat_path = Path(f"/proc/{pid}/stat")
        if stat_path.exists():
            parts = stat_path.read_text(encoding="utf-8", errors="ignore").split()
            if len(parts) > 2 and parts[2] == "Z":
                return False
        return True
    except OSError:
        return False


def _pid_matches_script(pid: int, script: str) -> bool:
    """Avoid killing an unrelated process if a stale PID was reused."""
    if os.name == "nt":
        return True
    cmdline_path = Path(f"/proc/{pid}/cmdline")
    if not cmdline_path.exists():
        return True
    try:
        raw = cmdline_path.read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="ignore")
    except OSError:
        return False
    expected = str(BASE_DIR / script)
    return script in raw and (str(BASE_DIR) in raw or expected in raw)


def _wait_stopped(pid: int, timeout_seconds: float) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not _pid_running(pid):
            return True
        time.sleep(0.2)
    return not _pid_running(pid)


def _start_one(name: str, script: str) -> dict:
    path = BASE_DIR / script
    creationflags = 0
    popen_kwargs = {
        "cwd": str(BASE_DIR),
    }
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_CONSOLE
        popen_kwargs["creationflags"] = creationflags
    else:
        # Detach child processes from the current SSH session and silence
        # inherited stdio so they keep running after the terminal closes.
        popen_kwargs.update({
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "start_new_session": True,
        })

    proc = subprocess.Popen(
        [sys.executable, str(path)],
        **popen_kwargs,
    )
    return {
        "pid": proc.pid,
        "script": script,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def start(open_browser: bool = True):
    state = _load_state()
    changed = False

    for name, script in PROGRAMS:
        old = state.get(name) or {}
        if _pid_running(old.get("pid")):
            print(f"{name}: already running pid={old['pid']}")
            continue
        info = _start_one(name, script)
        state[name] = info
        changed = True
        print(f"{name}: started pid={info['pid']}")
        time.sleep(0.8)

    if changed:
        _save_state(state)

    if open_browser:
        webbrowser.open(WEB_URL)
        print(f"browser: opened {WEB_URL}")


def _stop_pid(pid: int, script: str) -> str:
    if not _pid_running(pid):
        return "not_running"
    if not _pid_matches_script(pid, script):
        return "pid_mismatch"
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return "stopped" if _wait_stopped(pid, STOP_KILL_SECONDS) else "still_running"
    else:
        try:
            pgid = os.getpgid(pid)
        except OSError:
            return "not_running"

        # Processes are started with start_new_session=True, so the script and
        # Playwright children share a process group that can be stopped together.
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return "not_running"

        if _wait_stopped(pid, STOP_GRACE_SECONDS):
            return "stopped"

        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            return "stopped"

        return "killed" if _wait_stopped(pid, STOP_KILL_SECONDS) else "still_running"


def stop():
    state = _load_state()
    if not state:
        print("no saved processes")
        return

    all_stopped = True
    for name, script in reversed(PROGRAMS):
        info = state.get(name) or {}
        pid = info.get("pid")
        if not pid:
            print(f"{name}: no pid")
            continue
        result = _stop_pid(int(pid), script)
        if result == "stopped":
            print(f"{name}: stopped pid={pid}")
        elif result == "killed":
            print(f"{name}: killed pid={pid}")
        elif result == "pid_mismatch":
            all_stopped = False
            print(f"{name}: pid={pid} does not match {script}; not stopping")
        elif result == "still_running":
            all_stopped = False
            print(f"{name}: still running pid={pid}")
        else:
            print(f"{name}: not running pid={pid}")

    if all_stopped and PID_FILE.exists():
        PID_FILE.unlink()


def status():
    state = _load_state()
    if not state:
        print("no saved processes")
        return
    for name, script in PROGRAMS:
        info = state.get(name) or {}
        pid = info.get("pid")
        running = _pid_running(pid)
        print(f"{name}: {'running' if running else 'stopped'} pid={pid} script={script}")


def restart(open_browser: bool = True):
    stop()
    time.sleep(1)
    start(open_browser=open_browser)


def main():
    parser = argparse.ArgumentParser(description="Manage Binance monitor processes.")
    parser.add_argument(
        "command",
        choices=("start", "stop", "restart", "status"),
        help="Action to perform.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open the web page after start/restart.",
    )
    args = parser.parse_args()

    if args.command == "start":
        start(open_browser=not args.no_browser)
    elif args.command == "stop":
        stop()
    elif args.command == "restart":
        restart(open_browser=not args.no_browser)
    elif args.command == "status":
        status()


if __name__ == "__main__":
    main()
