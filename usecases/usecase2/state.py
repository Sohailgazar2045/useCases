"""state.py — the shared LangGraph state object for UC2 (Cash Application).

Every node reads from and writes to this single ``CashAppState`` dict as the
payment flows ingest → extract → match → decision → (auto-post | human review)
→ post. See UC2_EXECUTION_PLAN.md §2.5.
"""

from __future__ import annotations

from typing import TypedDict


class CashAppState(TypedDict, total=False):
    """State passed between graph nodes. ``total=False`` so nodes can populate
    their slice incrementally as the payment moves through the graph."""

    # ── ingest (Module 2) ────────────────────────────────────────────────
    document_path: str
    document_type: str            # "remittance" | "check" | "wire" | "card"
    raw_text: str                 # extracted text (txt / text-PDF)
    images: list                  # data-URIs for scans/images (scanned PDF, photo of check)

    # ── extract (Module 3, OpenAI) ───────────────────────────────────────
    extracted: dict               # {customer, payment_channel, currency, date, total_amount,
                                  #  invoices:[{invoice_no, amount_applied, deductions:[{amount,reason_code,note}]}]}
    extract_confidence: float

    # ── match (Module 4) ─────────────────────────────────────────────────
    candidate_invoices: list      # open invoices pulled from DB for this customer
    match_result: dict            # {situation, lines[], allocation[], disputes[], gap, customer_id}
    match_confidence: float

    # ── decision (Module 5) ──────────────────────────────────────────────
    route: str                    # "auto_post" | "human_review"
    recommendation: dict          # what the AI proposes (shown to the human)

    # ── human (Module 7) ─────────────────────────────────────────────────
    human_decision: dict          # {action: approve|adjust|reject, adjustments}

    # ── post (Module 8) ──────────────────────────────────────────────────
    posting_result: dict          # {status, invoice_ids, applied_amounts, timestamp}
    audit_log: list               # every step recorded


def new_state(document_path: str) -> "CashAppState":
    """Return a fresh state seeded with just the incoming document path."""
    return CashAppState(document_path=document_path, audit_log=[])
