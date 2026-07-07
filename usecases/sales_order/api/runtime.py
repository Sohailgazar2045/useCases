"""
api/runtime.py — auto-start the FastAPI backend from the Streamlit process.

Lets the whole use case run with a single command (`streamlit run Home.py`):
when the UI loads it checks whether the API is reachable and, if not, spawns
uvicorn as a background subprocess bound to the configured host/port.

If you prefer to run the API yourself (e.g. with --reload), just start it first;
`ensure_api_running()` sees it's already up and does nothing.
"""

from __future__ import annotations

import atexit
import os
import subprocess
import sys
import time
from urllib.parse import urlparse

import requests

from shared.config import API_BASE_URL

# Repo root = four levels up from this file (…/usecases/sales_order/api/runtime.py).
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_APP_PATH = "usecases.sales_order.api.main:app"
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0"}

# Module-level handle persists across Streamlit reruns (same interpreter).
_proc: subprocess.Popen | None = None


def _host_port() -> tuple[str, int]:
    parsed = urlparse(API_BASE_URL)
    return (parsed.hostname or "127.0.0.1"), (parsed.port or 8000)


def api_is_up() -> bool:
    try:
        return requests.get(f"{API_BASE_URL}/health", timeout=1).status_code == 200
    except requests.RequestException:
        return False


def _stop() -> None:
    global _proc
    if _proc is not None and _proc.poll() is None:
        _proc.terminate()
        try:
            _proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _proc.kill()


def ensure_api_running(timeout: float = 30.0) -> bool:
    """Return True once the API answers /health, starting it if needed.

    Only auto-starts when the API URL points at this machine; a remote
    API_BASE_URL is left untouched (we just report reachability).
    """
    global _proc

    if api_is_up():
        return True

    host, port = _host_port()
    if host not in _LOCAL_HOSTS:
        return False  # remote API we can't (and shouldn't) launch

    # Spawn uvicorn once; reuse the handle across reruns.
    if _proc is None or _proc.poll() is not None:
        _proc = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn", _APP_PATH,
                "--host", host, "--port", str(port), "--log-level", "warning",
            ],
            cwd=_REPO_ROOT,
        )
        atexit.register(_stop)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if api_is_up():
            return True
        if _proc.poll() is not None:  # process died during startup
            return False
        time.sleep(0.5)
    return api_is_up()
