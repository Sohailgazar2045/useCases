"""graph.py — Module 6: LangGraph assembly (nodes + conditional edges + checkpointer).

Wires the six-stage flow. Auto-post skips the human node; everything else pauses
at ``human_review`` via ``interrupt()`` and resumes on the same thread.

    ingest → extract → match → decision ──auto_post──────────────► post → END
                                      └────human_review──► human_review ─┘

Checkpointer: ``MemorySaver`` for the demo (Phase 4 switches to ``SqliteSaver``
so a pause survives a process restart).
"""

from __future__ import annotations

from .state import CashAppState
from .nodes.ingest import ingest
from .nodes.extract import extract
from .nodes.match import match
from .nodes.decision import decide, route_edge
from .nodes.human_review import human_review
from .nodes.post import post


def build_graph(checkpointer=None):
    """Assemble and compile the LangGraph. Pass a checkpointer to enable
    interrupt/resume (required for the HITL branch)."""
    from langgraph.graph import StateGraph, START, END
    from langgraph.checkpoint.memory import MemorySaver

    g = StateGraph(CashAppState)
    g.add_node("ingest", ingest)
    g.add_node("extract", extract)
    g.add_node("match", match)
    g.add_node("decision", decide)
    g.add_node("human_review", human_review)
    g.add_node("post", post)

    g.add_edge(START, "ingest")
    g.add_edge("ingest", "extract")
    g.add_edge("extract", "match")
    g.add_edge("match", "decision")
    g.add_conditional_edges(
        "decision",
        route_edge,
        {"auto_post": "post", "human_review": "human_review"},
    )
    g.add_edge("human_review", "post")
    g.add_edge("post", END)

    return g.compile(checkpointer=checkpointer or MemorySaver())


if __name__ == "__main__":
    # Smoke test: build the graph and print its structure.
    app = build_graph()
    print(app.get_graph().draw_ascii())
