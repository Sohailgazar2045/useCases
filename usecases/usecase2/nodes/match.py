"""match.py — Module 4: the tiered matching engine (the heart of UC2).

Takes the extracted remittance (per-invoice amounts + deductions with reason
codes) and classifies each invoice line, then the payment overall. This is where
the long tail is actually reasoned about — short pay vs. installment vs. credit
vs. dispute — not just detected. The logic is deterministic and unit-tested; the
AI reads the paper, the engine decides the money.

Per-line situations:            Payment-level situations add:
  EXACT      paid in full         NO_REFERENCE  no invoice # → allocation search
  OVERPAY    paid more            MULTI_INVOICE covers several invoices
  PARTIAL    installment          EXCEPTION     nothing matches
  CREDIT     took a credit
  DISPUTE    short w/ reason
  SHORT_PAY  short, no reason
"""

from __future__ import annotations

from itertools import combinations

from ..config import AMOUNT_MATCH_TOLERANCE, DISPUTE_REASONS
from ..db import get_open_invoices, resolve_customer
from ..state import CashAppState

# Situations
EXACT = "exact"
OVERPAY = "overpay"
PARTIAL = "partial"
CREDIT = "credit"
DISPUTE = "dispute"
SHORT_PAY = "short_pay"
NO_REFERENCE = "no_reference"
MULTI_INVOICE = "multi_invoice"
EXCEPTION = "exception"

# Situations that are safe to auto-post (all others → human review).
AUTO_POSTABLE = {EXACT}


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= AMOUNT_MATCH_TOLERANCE


def _subset_sum(candidates: list[dict], target: float, max_k: int = 6) -> list[dict] | None:
    """Return a subset of candidate invoices whose balances sum to ``target``.
    Used for the no-reference allocation search. Bounded by ``max_k``."""
    for k in range(1, min(max_k, len(candidates)) + 1):
        for combo in combinations(candidates, k):
            if _close(sum(c["balance"] for c in combo), target):
                return list(combo)
    return None


def _classify_line(line: dict, invoice: dict) -> dict:
    """Classify one referenced invoice line against its open balance."""
    balance = float(invoice["balance"])
    applied = float(line.get("amount_applied", 0.0))
    deductions = line.get("deductions", []) or []
    ded_total = round(sum(float(d.get("amount", 0.0)) for d in deductions), 2)
    gap = round(balance - applied, 2)
    codes = {d.get("reason_code", "UNKNOWN") for d in deductions}

    if _close(applied, balance) and not deductions:
        situation = EXACT
    elif applied > balance + AMOUNT_MATCH_TOLERANCE:
        situation = OVERPAY
    elif not deductions:
        situation = SHORT_PAY                     # short, no stated reason
    elif codes <= {"CREDIT"}:
        situation = CREDIT                        # took a credit they hold
    elif codes <= {"PARTIAL"}:
        situation = PARTIAL                       # intentional installment
    else:
        situation = DISPUTE                       # short-ship / damage / pricing / tax / mixed

    return {
        "invoice_no": invoice["invoice_no"],
        "balance": balance,
        "amount_applied": applied,
        "gap": gap,
        "deductions": deductions,
        "deduction_total": ded_total,
        "unexplained_gap": round(gap - ded_total, 2),   # gap not covered by stated deductions
        "situation": situation,
    }


def classify(extracted: dict, candidates: list[dict]) -> dict:
    """Given extracted remittance + open invoices, decide the situation and
    propose an allocation (what to post) plus disputes (what to open)."""
    by_no = {str(c["invoice_no"]).upper(): c for c in candidates}
    invoice_lines = extracted.get("invoices", []) or []
    total = float(extracted.get("total_amount", 0.0))

    # ── No invoice referenced → allocation search over open invoices ──────
    if not invoice_lines:
        subset = _subset_sum(candidates, total)
        if subset:
            situation = NO_REFERENCE if len(subset) == 1 else MULTI_INVOICE
            return {
                "situation": situation,
                "lines": [],
                "allocation": [{"invoice_no": c["invoice_no"], "amount": c["balance"]} for c in subset],
                "disputes": [],
                "gap": 0.0,
            }
        return {"situation": EXCEPTION, "lines": [], "allocation": [], "disputes": [], "gap": total}

    # ── Referenced invoices → classify each line ─────────────────────────
    lines: list[dict] = []
    for line in invoice_lines:
        inv = by_no.get(str(line.get("invoice_no", "")).upper())
        if inv is None:
            lines.append({
                "invoice_no": line.get("invoice_no"),
                "balance": None,
                "amount_applied": float(line.get("amount_applied", 0.0)),
                "gap": None,
                "deductions": line.get("deductions", []),
                "deduction_total": 0.0,
                "unexplained_gap": None,
                "situation": EXCEPTION,
            })
        else:
            lines.append(_classify_line(line, inv))

    # Build what to post (allocation) and what to open (disputes).
    allocation: list[dict] = []
    disputes: list[dict] = []
    for ln in lines:
        if ln["situation"] == EXCEPTION:
            continue
        post_amt = min(ln["amount_applied"], ln["balance"])
        if post_amt > 0:
            allocation.append({"invoice_no": ln["invoice_no"], "amount": round(post_amt, 2)})
        for d in ln["deductions"]:
            code = d.get("reason_code", "UNKNOWN")
            if code in DISPUTE_REASONS or code == "CREDIT":
                disputes.append({
                    "invoice_no": ln["invoice_no"],
                    "amount": float(d.get("amount", 0.0)),
                    "reason_code": code,
                    "note": d.get("note", ""),
                })

    # ── Overall situation ────────────────────────────────────────────────
    sits = [ln["situation"] for ln in lines]
    if len(lines) == 1:
        situation = sits[0]
    elif all(s == EXACT for s in sits):
        situation = MULTI_INVOICE            # multiple full payments → confirm
    else:
        situation = MULTI_INVOICE            # mixed lines → human, detail in `lines`

    total_gap = round(sum(ln["gap"] for ln in lines if ln["gap"] is not None), 2)
    return {"situation": situation, "lines": lines, "allocation": allocation,
            "disputes": disputes, "gap": total_gap}


def match(state: CashAppState) -> CashAppState:
    """Graph node: resolve the customer, pull candidates, classify the payment."""
    extracted = state["extracted"]
    customer_id = resolve_customer(extracted.get("customer", "")) or extracted.get("customer", "")
    candidates = get_open_invoices(customer_id)
    result = classify(extracted, candidates)
    result["customer_id"] = customer_id
    return {**state, "candidate_invoices": candidates, "match_result": result}
