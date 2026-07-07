"""test_decision.py — the auto-post safety gate (Module 5).

Proves the conservative rule: a payment auto-posts ONLY when it is an exact
match, has no disputes, AND extraction confidence clears the threshold. Every
near-miss (low confidence, short pay, dispute) must fall back to human review.
Pure function — no DB, no LLM, no graph.
"""

from __future__ import annotations

from usecases.usecase2.config import AUTO_POST_CONFIDENCE_THRESHOLD
from usecases.usecase2.nodes.decision import decide
from usecases.usecase2.nodes.match import DISPUTE, EXACT, SHORT_PAY


def _state(situation, extract_conf, disputes=None, allocation=None):
    return {
        "extract_confidence": extract_conf,
        "match_result": {
            "situation": situation,
            "allocation": allocation or [{"invoice_no": "INV-1001", "amount": 5000.0}],
            "disputes": disputes or [],
            "lines": [],
            "gap": 0.0,
            "customer_id": "CUST001",
        },
    }


def test_exact_high_confidence_auto_posts():
    out = decide(_state(EXACT, 1.0))
    assert out["route"] == "auto_post"


def test_exact_but_low_confidence_refuses():
    # confidence just below the gate → must NOT auto-post
    out = decide(_state(EXACT, AUTO_POST_CONFIDENCE_THRESHOLD - 0.01))
    assert out["route"] == "human_review"


def test_exact_at_threshold_auto_posts():
    out = decide(_state(EXACT, AUTO_POST_CONFIDENCE_THRESHOLD))
    assert out["route"] == "auto_post"


def test_short_pay_never_auto_posts():
    out = decide(_state(SHORT_PAY, 1.0))
    assert out["route"] == "human_review"


def test_dispute_never_auto_posts():
    out = decide(_state(DISPUTE, 1.0, disputes=[{"invoice_no": "INV-1002", "amount": 200.0, "reason_code": "DAMAGE"}]))
    assert out["route"] == "human_review"


def test_exact_with_lingering_dispute_refuses():
    # even a nominally-exact situation must not auto-post if a dispute is attached
    out = decide(_state(EXACT, 1.0, disputes=[{"invoice_no": "INV-1001", "amount": 1.0, "reason_code": "PRICING"}]))
    assert out["route"] == "human_review"


def test_recommendation_carries_proposed_action():
    out = decide(_state(EXACT, 1.0))
    assert out["recommendation"]["proposed_action"]
    assert out["recommendation"]["situation"] == EXACT
