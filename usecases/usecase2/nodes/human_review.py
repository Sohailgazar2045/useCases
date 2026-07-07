"""human_review.py — Module 7: the human-in-the-loop node (interrupt / resume).

The demo's centerpiece. The node calls LangGraph ``interrupt()`` with the AI's
recommendation payload — the graph pauses and checkpoints its state. A human
supplies a decision (via the review UI), and the graph resumes with
``Command(resume=human_decision)`` on the same ``thread_id``.

Human actions:
    approve → post exactly as recommended
    adjust  → post the human-corrected allocation / disputes
    reject  → nothing posted (send to exceptions)
"""

from __future__ import annotations

from ..state import CashAppState

_VALID_ACTIONS = {"approve", "adjust", "reject"}


def human_review(state: CashAppState) -> CashAppState:
    """Graph node: pause for a human, then fold their decision into state."""
    from langgraph.types import interrupt  # lazy import so tests don't need langgraph

    decision = interrupt(state["recommendation"])  # ← graph pauses here until resumed

    # Normalize whatever the UI sent into a well-formed decision.
    if not isinstance(decision, dict):
        decision = {"action": "approve"}
    action = decision.get("action", "approve")
    if action not in _VALID_ACTIONS:
        action = "approve"
    decision = {**decision, "action": action}

    return {**state, "human_decision": decision}
