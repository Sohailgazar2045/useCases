"""config.py — UC3 (AP Invoice Matching) paths and thresholds.

UC3 performs a classic 3-way match (invoice ↔ purchase order ↔ goods receipt).
The matching itself is pure Python (no LLM needed), so this module only owns the
mock-ledger file paths and the decision thresholds. The single project-level
``.env`` is loaded here via python-dotenv so any config lives in one place and
resolves the same way locally and on Streamlit Cloud.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load the project-level .env once (mirrors shared/config.py). No UC3-specific
# secrets today, but this keeps env-based config consistent across use cases.
load_dotenv()

# Base URL of the UC3 FastAPI backend. Its own port (UC1 uses 8000) so both can
# run side by side from a single `streamlit run Home.py`. Override via env — set
# this to the public API URL when the backend is deployed separately (e.g. Render).
UC3_API_BASE_URL = os.getenv("UC3_API_BASE_URL", "http://localhost:8001")

# Allowed CORS origins for the API. Comma-separated list, or "*" for any. When
# the API is public, set this to your Streamlit domain to lock it down, e.g.
# UC3_CORS_ORIGINS="https://your-app.streamlit.app".
UC3_CORS_ORIGINS = [o.strip() for o in os.getenv("UC3_CORS_ORIGINS", "*").split(",") if o.strip()]

# ── Paths ────────────────────────────────────────────────────────────────
# Anchored to this file (not the process CWD) so the paths resolve correctly no
# matter where uvicorn/pytest is launched from — the repo-root-relative
# equivalent of usecases/usecase3/data/*.json.
UC3_DIR = Path(__file__).resolve().parent
DATA_DIR = UC3_DIR / "data"

FLAGGED_INVOICES_PATH = DATA_DIR / "flagged_invoices.json"   # exceptions awaiting review
POSTED_INVOICES_PATH = DATA_DIR / "posted_invoices.json"     # approved + paid (mock)
RECEIPTS_PATH = DATA_DIR / "receipts.json"                   # goods receipts (mock)

# ── Thresholds ───────────────────────────────────────────────────────────
# Unit-price comparisons tolerate this much rounding noise (currency minor units).
PRICE_MATCH_TOLERANCE = 0.01

# Days a goods receipt can go un-invoiced before it raises a "received-not-invoiced"
# alert (accrual / missing-invoice risk).
RECEIVED_NOT_INVOICED_THRESHOLD_DAYS = 30
