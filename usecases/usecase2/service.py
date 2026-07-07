"""service.py — the seam between the LangGraph pipeline and any front-end.

Phase 4: drives the interrupt/resume human-in-the-loop cycle. A payment is
`start()`ed; it either auto-posts (clean) or PAUSES at the human-review node and
returns the AI's recommendation. The UI later calls `resume()` with the human's
approve/adjust/reject decision, and the graph finishes.

Checkpoints are persisted to SQLite (keyed by ``thread_id``), so a pause survives
across Streamlit reruns / separate requests — the resume can happen in a totally
different call than the start.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from .config import DATA_DIR
from .graph import build_graph
from .state import new_state

CHECKPOINT_DB = DATA_DIR / "checkpoints.db"

# One app instance per process, wired to a persistent checkpointer.
_APP = None


def get_app():
    """Return the compiled graph, backed by a disk-persisted SqliteSaver."""
    global _APP
    if _APP is None:
        Path(CHECKPOINT_DB).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(CHECKPOINT_DB), check_same_thread=False)
        _APP = build_graph(checkpointer=SqliteSaver(conn))
    return _APP


def _interrupt_payload(result: dict):
    """Pull the recommendation the graph handed to interrupt(), if it paused."""
    interrupts = result.get("__interrupt__")
    if not interrupts:
        return None
    first = interrupts[0]
    return getattr(first, "value", first)


def start(document_path: str, thread_id: str) -> dict:
    """Run a payment until it completes (auto-post) or pauses for a human.

    Returns:
      {"status": "auto_posted", "final": <state>}                     — clean, no human
      {"status": "paused", "recommendation": <payload>, "thread_id"}  — needs review
    """
    app = get_app()
    config = {"configurable": {"thread_id": thread_id}}
    result = app.invoke(new_state(document_path), config)

    payload = _interrupt_payload(result)
    if payload is not None:
        return {"status": "paused", "recommendation": payload, "thread_id": thread_id}
    return {"status": "auto_posted", "final": result, "thread_id": thread_id}


def resume(thread_id: str, decision: dict) -> dict:
    """Resume a paused payment with the human's decision and finish posting.

    ``decision`` = {"action": "approve" | "adjust" | "reject", ...}
    Returns {"status": "completed", "final": <state>}.
    """
    app = get_app()
    config = {"configurable": {"thread_id": thread_id}}
    final = app.invoke(Command(resume=decision), config)
    return {"status": "completed", "final": final, "thread_id": thread_id}
