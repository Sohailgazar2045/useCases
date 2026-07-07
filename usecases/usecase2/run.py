"""run.py — Phase 2 runner: push one payment document through the graph.

This is the entry point that processes a single payment end-to-end:
ingest -> extract (OpenAI) -> match -> decision -> (auto-post | human review) -> post.

Usage:
    python -m usecases.usecase2.run                         # runs the clean_exact sample
    python -m usecases.usecase2.run path/to/payment.txt     # runs your own document

By default it reseeds the mock D365 first so the demo is repeatable. In
production you would NOT reseed — you'd process against live open invoices.
"""

from __future__ import annotations

import json
import sys

from langgraph.types import Command

from .config import SAMPLE_PAYMENTS_DIR
from .data.seed import seed
from .db import get_invoice
from .graph import build_graph
from .state import new_state


def run_payment(
    document_path: str,
    thread_id: str = "demo-1",
    reseed: bool = True,
    auto_approve: bool = True,
) -> dict:
    """Run one payment document through the compiled graph and return final state.

    Edge cases pause at the human-review node (``interrupt()``). Until Phase 4's
    interactive UI exists, ``auto_approve`` resumes them with a simulated human
    'approve' so the full resolution (post + open disputes) can be demonstrated.
    """
    if reseed:
        seed()
    app = build_graph()
    config = {"configurable": {"thread_id": thread_id}}
    final = app.invoke(new_state(document_path), config)
    if auto_approve and "__interrupt__" in final:
        final = app.invoke(Command(resume={"action": "approve"}), config)
    return final


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else str(SAMPLE_PAYMENTS_DIR / "clean_exact.txt")
    print("== Phase 2 run " + "=" * 45)
    print(f"Document: {path}\n")

    final = run_payment(path)

    extracted = final.get("extracted", {})
    print("1) EXTRACT (OpenAI) ->")
    print("   " + json.dumps(extracted))
    print(f"   confidence: {final.get('extract_confidence')}\n")

    mr = final.get("match_result", {})
    print("2) MATCH ->")
    print(f"   situation: {mr.get('situation')}  gap: {mr.get('gap')}")
    for ln in mr.get("lines", []):
        ded = "".join(
            f"\n        - {d.get('amount')} {d.get('reason_code')} ({d.get('note','')})"
            for d in ln.get("deductions", [])
        )
        print(f"     {ln.get('invoice_no')}: applied {ln.get('amount_applied')} of "
              f"{ln.get('balance')} -> {ln.get('situation')}{ded}")
    print()

    rec = final.get("recommendation", {})
    print("3) DECISION ->")
    print(f"   route: {final.get('route')}  match_confidence: {final.get('match_confidence')}")
    print(f"   proposed: {rec.get('proposed_action')}\n")

    pr = final.get("posting_result", {})
    print("4) POST ->")
    print(f"   status: {pr.get('status')}  invoices posted: {pr.get('invoice_ids')}")
    for a in pr.get("applied_amounts", []):
        print(f"     applied {a['amount']} to {a['invoice_no']} -> balance {a['balance']:.2f}")
    for d in pr.get("disputes_opened", []):
        print(f"     opened dispute #{d.get('dispute_id')}: {d.get('amount')} {d.get('reason_code')} on {d.get('invoice_no')}")

    for inv_no in pr.get("invoice_ids", []):
        inv = get_invoice(inv_no)
        if inv:
            print(f"   {inv_no}: balance={inv['balance']:.2f} status={inv['status']}")

    if final.get("route") == "auto_post":
        print("\n[PASS] happy path auto-posted (no human needed)")
    else:
        print("\n[EDGE CASE] routed to human review; [human review simulated: APPROVE] -> "
              "resolution executed above (Phase 4 makes this an interactive step)")


if __name__ == "__main__":
    main()
