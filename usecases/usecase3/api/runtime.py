"""
api/runtime.py — auto-start the UC3 FastAPI backend from the Streamlit process.

Same mechanism as UC1's ``sales_order/api/runtime.py``: when the UI loads it
checks whether the API is reachable and, if not, spawns uvicorn as a background
subprocess bound to UC3's configured host/port (default :8001, so it coexists
with UC1's :8000). Lets the whole suite run from `streamlit run Home.py`.

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

from ..config import UC3_API_BASE_URL

# Repo root = four levels up from this file (…/usecases/usecase3/api/runtime.py).
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_APP_PATH = "usecases.usecase3.api.main:app"
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0"}

# Module-level handle persists across Streamlit reruns (same interpreter).
_proc: subprocess.Popen | None = None


def _host_port() -> tuple[str, int]:
    parsed = urlparse(UC3_API_BASE_URL)
    return (parsed.hostname or "127.0.0.1"), (parsed.port or 8001)


def api_is_up() -> bool:
    try:
        # 4s tolerates a remote host that's mid-cold-start (e.g. Render free tier).
        return requests.get(f"{UC3_API_BASE_URL}/health", timeout=4).status_code == 200
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

    Local URL: spawn uvicorn if it isn't already up. Remote URL (e.g. a Render
    deploy set via UC3_API_BASE_URL): we can't launch it, but it may be waking
    from a free-tier idle sleep — so poll until it answers or the timeout lapses.
    """
    global _proc

    if api_is_up():
        return True

    host, port = _host_port()
    if host not in _LOCAL_HOSTS:
        # Remote API — can't start it, but wait out a cold start (Render/Railway
        # free tiers sleep when idle and take ~30–50s to wake on first request).
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if api_is_up():
                return True
            time.sleep(2.0)
        return api_is_up()

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
