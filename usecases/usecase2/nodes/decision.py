"""decision.py — Module 5: confidence scoring + routing.

Assigns ``match_confidence`` per situation, applies the auto-post threshold, and
composes the ``recommendation`` payload the human sees (including a plain-English
proposed resolution for the long-tail cases). Exposes ``route_edge`` — the
LangGraph conditional-edge function.
"""

from __future__ import annotations

from ..config import AUTO_POST_CONFIDENCE_THRESHOLD
from ..state import CashAppState
from .match import (
    AUTO_POSTABLE,
    CREDIT,
    DISPUTE,
    EXACT,
    EXCEPTION,
    MULTI_INVOICE,
    NO_REFERENCE,
    OVERPAY,
    PARTIAL,
    SHORT_PAY,
)

# Baseline confidence per situation (tunable; human decisions feed back later).
_SITUATION_CONFIDENCE = {
    EXACT: 1.0,
    PARTIAL: 0.7,
    CREDIT: 0.65,
    DISPUTE: 0.6,
    SHORT_PAY: 0.5,
    OVERPAY: 0.55,
    NO_REFERENCE: 0.5,
    MULTI_INVOICE: 0.55,
    EXCEPTION: 0.2,
}

# What the AI proposes doing for each situation (shown to the human).
_PROPOSED_ACTION = {
    EXACT: "Auto-post in full to the referenced invoice.",
    PARTIAL: "Post the paid amount; leave the invoice open for the remaining installment.",
    CREDIT: "Post the paid amount; open a CREDIT case for the deducted amount to reconcile against a credit memo.",
    DISPUTE: "Post the paid amount; open a dispute case for the withheld amount under its reason code.",
    SHORT_PAY: "Short paid with no stated reason — confirm with the customer or open an UNKNOWN dispute.",
    OVERPAY: "Post to the invoice; place the overpayment on account / open a credit.",
    NO_REFERENCE: "No invoice referenced — confirm the proposed allocation found by amount.",
    MULTI_INVOICE: "Confirm the proposed split across multiple invoices.",
    EXCEPTION: "No matching open invoice found — needs manual investigation.",
}


def decide(state: CashAppState) -> CashAppState:
    """Graph node: score, route, and build the recommendation."""
    mr = state["match_result"]
    extracted = state.get("extracted", {})
    situation = mr["situation"]
    extract_conf = float(state.get("extract_confidence", 0.0))
    match_conf = _SITUATION_CONFIDENCE.get(situation, 0.3)

    # Auto-post ONLY when: exact, no disputes, and extraction confidence clears the gate.
    auto = (
        situation in AUTO_POSTABLE
        and not mr.get("disputes")
        and extract_conf >= AUTO_POST_CONFIDENCE_THRESHOLD
    )
    route = "auto_post" if auto else "human_review"

    recommendation = {
        "situation": situation,
        "route": route,
        "proposed_action": _PROPOSED_ACTION.get(situation, "Review."),
        "match_confidence": match_conf,
        "extract_confidence": extract_conf,
        "proposed_allocation": mr.get("allocation", []),
        "proposed_disputes": mr.get("disputes", []),
        "lines": mr.get("lines", []),
        "gap": mr.get("gap"),
        "customer_id": mr.get("customer_id"),
        "payment": {
            "customer": extracted.get("customer"),
            "customer_id": mr.get("customer_id"),
            "total_amount": extracted.get("total_amount"),
            "currency": extracted.get("currency", "USD"),
            "date": extracted.get("date"),
            "channel": extracted.get("payment_channel"),
        },
        # the original document, so the review screen can display it
        "document_text": state.get("raw_text", ""),
        "document_images": state.get("images", []),
    }
    return {**state, "match_confidence": match_conf, "route": route, "recommendation": recommendation}


def route_edge(state: CashAppState) -> str:
    """LangGraph conditional edge: returns the next node key based on ``route``."""
    return state["route"]
