"""config.py — UC2 (Cash Application) settings and thresholds.

UC2 uses OpenAI (the same provider and key as Use Case 1) for extraction and
ambiguous-case reasoning. The LLM client itself is shared from
``shared/config.py`` so there's one place that handles the key; this module only
adds UC2-specific paths and decision thresholds.
"""

from __future__ import annotations

from pathlib import Path

from openai import OpenAI

# Same key path as Use Case 1: shared.config.get_openai_api_key() reads
# OPENAI_API_KEY from the environment (Streamlit Cloud secrets are exposed as
# env vars), so both use cases resolve the key identically.
from shared.config import OPENAI_MODEL, get_openai_api_key

LLM_MODEL = OPENAI_MODEL


def get_llm_client() -> OpenAI:
    """OpenAI client built with the shared key — identical to UC1's key path."""
    return OpenAI(api_key=get_openai_api_key())

# ── Paths ────────────────────────────────────────────────────────────────
UC2_DIR = Path(__file__).resolve().parent
DATA_DIR = UC2_DIR / "data"
DB_PATH = DATA_DIR / "mock_d365.db"          # mock system of record (generated)
SAMPLE_PAYMENTS_DIR = DATA_DIR / "sample_payments"

# ── Decision thresholds (UC2_EXECUTION_PLAN.md §2.4) ──────────────────────
# Auto-post ONLY when: invoice # referenced AND amount exact AND extraction
# confidence ≥ this value. Everything else routes to human review.
AUTO_POST_CONFIDENCE_THRESHOLD = 0.95

# Amount comparisons tolerate this much rounding noise (currency minor units).
AMOUNT_MATCH_TOLERANCE = 0.01

# ── Deduction reason codes (the "why" behind a short pay) ─────────────────
# The extractor tags each deduction with one of these; the matching engine uses
# them to tell a genuine dispute from an installment from a credit.
REASON_CODES = {
    "SHORT_SHIP": "Customer did not receive part of the order (missing units/lines).",
    "DAMAGE": "Goods arrived damaged.",
    "PRICING": "Price billed differs from the agreed/PO price.",
    "TAX": "Tax billed incorrectly.",
    "CREDIT": "Customer applied an existing credit / credit memo they hold.",
    "PARTIAL": "Intentional installment — paying some now, the rest later (not a dispute).",
    "UNKNOWN": "Short paid with no stated reason.",
}

# Reason codes that represent a genuine dispute needing AR follow-up (vs. an
# installment, which just leaves the invoice partly open).
DISPUTE_REASONS = {"SHORT_SHIP", "DAMAGE", "PRICING", "TAX", "UNKNOWN"}
