"""test_matching.py — Module 4 unit tests for the tiered matching engine.

``classify`` is pure (extracted remittance + candidate invoices in, result out),
so these run with no DB, no LLM, no LangGraph.
Run:  pytest usecases/usecase2/tests
"""

from __future__ import annotations

from usecases.usecase2.nodes.match import (
    CREDIT,
    DISPUTE,
    EXACT,
    EXCEPTION,
    MULTI_INVOICE,
    NO_REFERENCE,
    OVERPAY,
    PARTIAL,
    SHORT_PAY,
    classify,
)

CANDIDATES = [
    {"invoice_no": "INV-1001", "balance": 5000.00},
    {"invoice_no": "INV-1002", "balance": 3200.00},
    {"invoice_no": "INV-1003", "balance": 1500.00},
]


def _remit(invoices, total=None):
    total = total if total is not None else sum(i["amount_applied"] for i in invoices)
    return {"total_amount": total, "invoices": invoices}


def test_exact():
    r = classify(_remit([{"invoice_no": "INV-1001", "amount_applied": 5000.0, "deductions": []}]), CANDIDATES)
    assert r["situation"] == EXACT
    assert r["allocation"] == [{"invoice_no": "INV-1001", "amount": 5000.0}]
    assert r["disputes"] == []


def test_dispute_short_with_reason():
    r = classify(_remit([{
        "invoice_no": "INV-1002", "amount_applied": 3000.0,
        "deductions": [{"amount": 200.0, "reason_code": "DAMAGE", "note": "broken"}],
    }]), CANDIDATES)
    assert r["situation"] == DISPUTE
    assert r["allocation"] == [{"invoice_no": "INV-1002", "amount": 3000.0}]
    assert r["disputes"][0]["reason_code"] == "DAMAGE"
    assert r["disputes"][0]["amount"] == 200.0


def test_credit_taken():
    r = classify(_remit([{
        "invoice_no": "INV-1001", "amount_applied": 4000.0,
        "deductions": [{"amount": 1000.0, "reason_code": "CREDIT", "note": "memo"}],
    }]), CANDIDATES)
    assert r["situation"] == CREDIT
    assert r["disputes"][0]["reason_code"] == "CREDIT"


def test_partial_installment_opens_no_dispute():
    r = classify(_remit([{
        "invoice_no": "INV-1001", "amount_applied": 2000.0,
        "deductions": [{"amount": 3000.0, "reason_code": "PARTIAL", "note": "rest later"}],
    }]), CANDIDATES)
    assert r["situation"] == PARTIAL
    assert r["disputes"] == []                       # installment is NOT a dispute
    assert r["allocation"] == [{"invoice_no": "INV-1001", "amount": 2000.0}]


def test_short_pay_no_reason():
    r = classify(_remit([{"invoice_no": "INV-1002", "amount_applied": 3000.0, "deductions": []}]), CANDIDATES)
    assert r["situation"] == SHORT_PAY               # short but no reason given


def test_overpay():
    r = classify(_remit([{"invoice_no": "INV-1003", "amount_applied": 2000.0, "deductions": []}]), CANDIDATES)
    assert r["situation"] == OVERPAY


def test_no_reference_allocation():
    r = classify(_remit([], total=1500.0), CANDIDATES)
    assert r["situation"] == NO_REFERENCE
    assert r["allocation"] == [{"invoice_no": "INV-1003", "amount": 1500.0}]


def test_no_reference_subset_sum_multi():
    r = classify(_remit([], total=8200.0), CANDIDATES)   # 5000 + 3200
    assert r["situation"] == MULTI_INVOICE
    assert {a["invoice_no"] for a in r["allocation"]} == {"INV-1001", "INV-1002"}


def test_multi_invoice_referenced():
    r = classify(_remit([
        {"invoice_no": "INV-1001", "amount_applied": 5000.0, "deductions": []},
        {"invoice_no": "INV-1002", "amount_applied": 3200.0, "deductions": []},
    ]), CANDIDATES)
    assert r["situation"] == MULTI_INVOICE


def test_exception_unknown_invoice():
    r = classify(_remit([{"invoice_no": "INV-9999", "amount_applied": 42.0, "deductions": []}]), CANDIDATES)
    assert r["situation"] == EXCEPTION
