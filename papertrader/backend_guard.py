"""
Backend availability guard for cron-driven scan jobs.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import requests

from .config import API_BASE


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
BACKEND_MAIN = BACKEND_DIR / "main.py"


def _health_url() -> str:
    return f"{API_BASE.rstrip('/')}/health"


def is_backend_healthy(timeout_sec: float = 3.0) -> bool:
    try:
        res = requests.get(_health_url(), timeout=timeout_sec)
        return res.status_code == 200
    except Exception:
        return False


def _spawn_backend() -> bool:
    if not BACKEND_MAIN.exists():
        return False

    log_path = PROJECT_ROOT / "papertrader" / "backend_guard.log"
    with open(log_path, "a", encoding="utf-8") as log_file:
        subprocess.Popen(
            [sys.executable, "main.py"],
            cwd=str(BACKEND_DIR),
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
        )
    return True


def ensure_backend_available(
    autostart: bool | None = None,
    wait_sec: float | None = None,
    poll_sec: float | None = None,
    health_timeout_sec: float | None = None,
) -> tuple[bool, str]:
    if health_timeout_sec is None:
        health_timeout_sec = float(os.environ.get("PT_BACKEND_HEALTH_TIMEOUT_SEC", "3"))
    if wait_sec is None:
        wait_sec = float(os.environ.get("PT_BACKEND_START_WAIT_SEC", "25"))
    if poll_sec is None:
        poll_sec = float(os.environ.get("PT_BACKEND_BOOT_POLL_SEC", "1"))
    if autostart is None:
        autostart = os.environ.get("PT_BACKEND_AUTOSTART", "1") == "1"

    if is_backend_healthy(timeout_sec=health_timeout_sec):
        return True, "Backend healthy."

    if not autostart:
        return False, "Backend down and autostart disabled (PT_BACKEND_AUTOSTART=0)."

    started = _spawn_backend()
    if not started:
        return False, "Backend down and startup failed (missing backend/main.py)."

    deadline = time.time() + wait_sec
    while time.time() < deadline:
        time.sleep(max(poll_sec, 0.2))
        if is_backend_healthy(timeout_sec=health_timeout_sec):
            return True, "Backend was down; started successfully."

    return False, f"Backend startup attempted but health endpoint did not recover within {wait_sec:.0f}s."
