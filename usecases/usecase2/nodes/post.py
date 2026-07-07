"""post.py — Module 8: posting + audit.

Applies the payment to the mock D365 (SQLite), opens dispute/credit cases for any
withheld amounts, and writes the audit trail. On auto-post it posts the AI's
recommended allocation; on the human branch it posts exactly what the human
approved/adjusted and logs the human action as feedback (the "learn" step).
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..db import apply_payment, record_dispute, record_payment, write_audit
from ..state import CashAppState


def _allocation_to_post(state: CashAppState) -> list[dict]:
    """Choose the allocation to post: human decision wins, else recommendation."""
    decision = state.get("human_decision") or {}
    if decision.get("action") == "reject":
        return []
    if decision.get("action") == "adjust" and decision.get("adjustments"):
        return decision["adjustments"]
    # approve, or auto-post branch (no human decision)
    return state["recommendation"].get("proposed_allocation", [])


def _disputes_to_open(state: CashAppState) -> list[dict]:
    """Disputes to open: a human 'adjust' can override, else the recommendation."""
    decision = state.get("human_decision") or {}
    if decision.get("action") == "reject":
        return []
    if decision.get("action") == "adjust" and "disputes" in decision:
        return decision["disputes"]
    return state["recommendation"].get("proposed_disputes", [])


def post(state: CashAppState) -> CashAppState:
    """Graph node: apply the payment, open disputes, and record the audit trail."""
    allocation = _allocation_to_post(state)
    disputes = _disputes_to_open(state)
    extracted = state.get("extracted", {})
    customer_id = state.get("recommendation", {}).get("customer_id") or extracted.get("customer", "UNKNOWN")

    # Record the incoming payment itself (once), then apply it to the invoice(s).
    if allocation:
        record_payment(
            customer_id,
            float(extracted.get("total_amount", 0.0)),
            currency=extracted.get("currency", "USD"),
            received_date=extracted.get("date"),
            source_doc=state.get("document_path"),
        )

    applied: list[dict] = []
    for line in allocation:
        updated = apply_payment(line["invoice_no"], float(line["amount"]))
        applied.append({"invoice_no": line["invoice_no"], "amount": line["amount"], "balance": updated["balance"]})

    opened_disputes: list[dict] = []
    for d in disputes:
        dispute_id = record_dispute(
            d.get("invoice_no"), customer_id, float(d.get("amount", 0.0)),
            d.get("reason_code", "UNKNOWN"), d.get("note", ""),
        )
        opened_disputes.append({**d, "dispute_id": dispute_id})

    result = {
        "status": "rejected" if not allocation and not opened_disputes else "posted",
        "invoice_ids": [a["invoice_no"] for a in applied],
        "applied_amounts": applied,
        "disputes_opened": opened_disputes,
        "route": state.get("route"),
        "human_decision": state.get("human_decision"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    write_audit("post", result)
    return {**state, "posting_result": result}
