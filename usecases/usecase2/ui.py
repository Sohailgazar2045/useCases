"""ui.py — Module 9: the Cash Application review surface (hub entry point).

A payment document arrives (uploaded PDF / text / image). The AI extracts and
matches it; clean exact matches auto-post untouched, while the long tail pops a
**modal approval dialog** showing the AI's recommendation + evidence with
Approve / Adjust / Reject — which resume the paused LangGraph run (interrupt/
resume via SqliteSaver).

Exposes ``render()`` so the hub page wrapper (pages/2_Use_Case_2.py) mounts it.
"""

from __future__ import annotations

import base64
import glob
import os
from pathlib import Path

import streamlit as st

from .config import DATA_DIR, DB_PATH, SAMPLE_PAYMENTS_DIR
from .data.seed import seed as seed_db
from .db import _connect, get_audit_log, get_disputes, get_open_invoices
from .service import resume as svc_resume
from .service import start as svc_start

_CUSTOMERS = ["CUST001", "CUST002", "CUST003"]
_UPLOADS_DIR = DATA_DIR / "uploads"

_SCENARIOS = {
    "clean_exact.txt": ("Clean exact match", "Auto-posts — no human needed"),
    "short_pay_damage.txt": ("Short pay — damage", "Withheld with a DAMAGE reason → dispute"),
    "short_pay_unknown.txt": ("Short pay — no reason", "Short with no stated reason"),
    "credit_taken.txt": ("Credit memo applied", "Customer applied a credit they hold"),
    "partial_installment.txt": ("Partial / installment", "Paying some now, the rest later"),
    "multi_invoice.txt": ("Multi-invoice payment", "One payment across several invoices"),
    "no_reference.txt": ("No invoice reference", "No invoice # → allocation search"),
    "overpay.txt": ("Overpayment", "Paid more than the balance"),
    "exception_unknown_invoice.txt": ("Exception — unknown invoice", "References an invoice we don't have"),
    "wire_multi_pricing.txt": ("Wire — multi-invoice + pricing", "Wire covering two invoices, one pricing dispute"),
}

_STYLE = {
    "exact": ("✅", "green"), "dispute": ("⚠️", "red"), "credit": ("💳", "violet"),
    "partial": ("⏳", "blue"), "short_pay": ("❓", "orange"), "overpay": ("⬆️", "orange"),
    "no_reference": ("🔎", "gray"), "multi_invoice": ("🧾", "blue"), "exception": ("⛔", "red"),
}


# ── helpers ──────────────────────────────────────────────────────────────
def _ensure_seeded() -> None:
    if not DB_PATH.exists():
        seed_db()
        return
    try:
        with _connect() as conn:
            n = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        if not n:
            seed_db()
    except Exception:
        seed_db()


def _reset(to: str = "idle") -> None:
    for k in ("thread_id", "doc", "recommendation", "result"):
        st.session_state.pop(k, None)
    st.session_state["phase"] = to


def _badge(situation: str) -> str:
    emoji, color = _STYLE.get(situation, ("•", "gray"))
    return f":{color}-background[{emoji} {str(situation).replace('_', ' ').title()}]"


def _money(v) -> str:
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return str(v)


def _save_upload(uploaded) -> str:
    _UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    dest = _UPLOADS_DIR / uploaded.name
    dest.write_bytes(uploaded.getbuffer())
    return str(dest)


def _process(path: str) -> None:
    st.session_state["counter"] = st.session_state.get("counter", 0) + 1
    tid = f"{os.path.basename(path)}-{st.session_state['counter']}"
    with st.spinner("AI reading the document and matching it against open invoices…"):
        res = svc_start(path, tid)
    st.session_state["thread_id"] = tid
    st.session_state["doc"] = path
    if res["status"] == "auto_posted":
        st.session_state["result"] = {"auto": True, "final": res["final"]}
        st.session_state["phase"] = "done"
    else:
        st.session_state["recommendation"] = res["recommendation"]
        st.session_state["phase"] = "paused"
    st.rerun()


def _do_resume(decision: dict) -> None:
    out = svc_resume(st.session_state["thread_id"], decision)
    st.session_state["result"] = {"auto": False, "decision": decision, "final": out["final"]}
    st.session_state["phase"] = "done"
    st.rerun()


# ── the modal approval dialog — JUST the decision (approve / reject) ─────
@st.dialog("Confirm your decision")
def _approval_dialog() -> None:
    rec = st.session_state["recommendation"]
    st.markdown(f"You're about to **{('approve — ' + rec.get('proposed_action', '')).rstrip('.')}.**")
    st.caption("Approve posts the payment as recommended. Reject posts nothing and "
               "sends it to the exceptions queue.")
    st.write("")
    approve, reject = st.columns(2)
    if approve.button("✅ Approve", type="primary", width="stretch"):
        _do_resume({"action": "approve"})
    if reject.button("❌ Reject", width="stretch"):
        _do_resume({"action": "reject"})


def _plain_summary(rec: dict) -> str:
    """One friendly sentence a non-specialist can read at a glance."""
    pay = rec.get("payment", {})
    total = _money(pay.get("total_amount"))
    who = pay.get("customer") or pay.get("customer_id") or "The customer"
    real_lines = [ln for ln in rec.get("lines", []) if ln.get("balance") is not None]
    if len(real_lines) == 1:
        ln = real_lines[0]
        context = (f"{who} paid **{total}** toward invoice **{ln['invoice_no']}** "
                   f"(open balance {_money(ln['balance'])}).")
    elif len(real_lines) > 1:
        context = f"{who} paid **{total}** across **{len(real_lines)} invoices**."
    else:
        context = f"{who} paid **{total}**, with no invoice number on the document."
    return context + " " + rec.get("proposed_action", "")


def _render_document(rec: dict) -> None:
    """Show the actual payment document (text, or the scanned image)."""
    text = rec.get("document_text") or ""
    images = rec.get("document_images") or []
    if text.strip():
        st.code(text, language="text")
        return
    if images:
        for uri in images:
            try:
                st.image(base64.b64decode(uri.split(",", 1)[1]), width="stretch")
            except Exception:
                st.caption("(could not render image)")
        return
    try:
        st.code(Path(st.session_state["doc"]).read_text(encoding="utf-8", errors="ignore"), language="text")
    except Exception:
        st.caption("(document not previewable)")


# ── panels ───────────────────────────────────────────────────────────────
def _sidebar() -> None:
    with st.sidebar:
        st.header("🗄️ Mock D365")
        st.caption("System of record (SQLite standing in for Dynamics 365).")
        if st.button("↻ Reset demo data", width="stretch"):
            seed_db()
            _reset()
            st.rerun()
        for c in _CUSTOMERS:
            invoices = get_open_invoices(c)
            if invoices:
                st.markdown(f"**{c}** — open invoices")
                st.dataframe([{"invoice": i["invoice_no"], "balance": i["balance"], "status": i["status"]}
                              for i in invoices], hide_index=True, width="stretch")
        disputes = get_disputes()
        st.markdown("**Open disputes / credits**")
        if disputes:
            st.dataframe([{"#": d["dispute_id"], "invoice": d["invoice_no"], "amount": d["amount"],
                           "reason": d["reason_code"]} for d in disputes], hide_index=True, width="stretch")
        else:
            st.caption("None yet.")
        with st.expander("📜 Audit trail"):
            audit = get_audit_log()
            if not audit:
                st.caption("No activity yet.")
            for row in audit[-8:][::-1]:
                st.caption(f"{row['stage']} · {row['ts'][11:19]}")


def _panel_submit() -> None:
    st.subheader("1 · A payment arrives")
    c1, c2, c3 = st.columns(3)
    c1.markdown("**① Extract**\n\nAI reads the document")
    c2.markdown("**② Match**\n\nAgainst open invoices")
    c3.markdown("**③ Decide**\n\nAuto-post or review")

    st.caption("Drop in a remittance / payment document (**PDF, text, or image**). "
               "The AI extracts and matches it — no manual matching.")
    up = st.file_uploader("Upload payment document", type=["pdf", "txt", "png", "jpg", "jpeg"])
    if up is not None:
        st.success(f"Ready to process: **{up.name}**")
        if up.name.lower().endswith(".txt"):
            st.code(up.getvalue().decode("utf-8", "ignore"), language="text")
        if st.button("▶ Process payment", type="primary", width="stretch"):
            _process(_save_upload(up))

    with st.expander("…or try a built-in sample payment"):
        docs = sorted(glob.glob(str(SAMPLE_PAYMENTS_DIR / "*.txt")))
        if docs:
            choice = st.selectbox(
                "Sample scenario", docs,
                format_func=lambda p: _SCENARIOS.get(os.path.basename(p), (os.path.basename(p),))[0],
                key="sample_pick",
            )
            base = os.path.basename(choice)
            if base in _SCENARIOS:
                st.caption("Scenario: " + _SCENARIOS[base][1])
            if st.button("Use this sample"):
                _process(choice)


def _panel_review() -> None:
    rec = st.session_state["recommendation"]
    st.subheader("2 · Review this payment")

    # Plain-English banner anyone can read.
    st.info("🧾 " + _plain_summary(rec))

    left, right = st.columns(2, gap="large")

    with left:
        st.markdown("#### 📄 The payment document")
        _render_document(rec)

    with right:
        st.markdown("#### 🤖 What the AI found")
        pay = rec.get("payment", {})
        m1, m2 = st.columns(2)
        m1.metric("Customer", pay.get("customer_id") or pay.get("customer") or "—")
        m2.metric("Amount paid", _money(pay.get("total_amount")))
        st.markdown(f"Situation: {_badge(rec.get('situation'))} &nbsp; · &nbsp; "
                    f"Channel: {pay.get('channel') or '—'}")

        ec = float(rec.get("extract_confidence") or 0)
        mc = float(rec.get("match_confidence") or 0)
        st.caption(f"AI reading confidence — {ec:.0%}")
        st.progress(min(max(ec, 0.0), 1.0))
        st.caption(f"Match confidence — {mc:.0%}")
        st.progress(min(max(mc, 0.0), 1.0))

        st.markdown("**AI's suggestion**")
        st.success("💡 " + rec.get("proposed_action", ""))

        if rec.get("proposed_allocation"):
            st.markdown("**Money to post**")
            st.dataframe([{"invoice": x["invoice_no"], "apply": _money(x["amount"])}
                          for x in rec["proposed_allocation"]], hide_index=True, width="stretch")
        if rec.get("proposed_disputes"):
            st.markdown("**Cases to open**")
            st.dataframe([{"invoice": d["invoice_no"], "amount": _money(d["amount"]),
                           "reason": d["reason_code"], "note": d.get("note", "")}
                          for d in rec["proposed_disputes"]], hide_index=True, width="stretch")

    st.divider()
    st.markdown("#### ✅ Your decision")
    st.caption("Review the document and the AI's suggestion above, then decide.")
    d1, d2 = st.columns([2, 3])
    if d1.button("🔐 Approve or Reject", type="primary", width="stretch"):
        _approval_dialog()

    with st.expander("✏️ Adjust the amounts before deciding (optional)"):
        new_alloc = []
        for idx, line in enumerate(rec.get("proposed_allocation", [])):
            amt = st.number_input(f"Apply to {line['invoice_no']}", value=float(line["amount"]),
                                  min_value=0.0, step=100.0, key=f"adj_{idx}")
            new_alloc.append({"invoice_no": line["invoice_no"], "amount": amt})
        keep = []
        for idx, d in enumerate(rec.get("proposed_disputes", [])):
            if st.checkbox(f"Open {d['reason_code']} case for {_money(d['amount'])} on {d['invoice_no']}",
                           value=True, key=f"disp_{idx}"):
                keep.append(d)
        if st.button("Post this adjusted version"):
            _do_resume({"action": "adjust", "adjustments": new_alloc, "disputes": keep})


def _panel_result() -> None:
    result = st.session_state["result"]
    final = result["final"]
    pr = final.get("posting_result", {})
    st.subheader("3 · Done")

    if result.get("auto"):
        st.success("✅ Auto-posted — clean exact match, no human review needed.")
        st.balloons()
    else:
        action = result["decision"]["action"]
        if action == "reject":
            st.warning("❌ Rejected — nothing posted; routed to exceptions.")
        else:
            st.success(f"✅ Human {action.upper()} applied — resolution posted to the mock D365.")

    if pr.get("applied_amounts"):
        st.markdown("**Posted to invoices**")
        st.dataframe([{"invoice": a["invoice_no"], "applied": _money(a["amount"]),
                       "new balance": _money(a["balance"])} for a in pr["applied_amounts"]],
                     hide_index=True, width="stretch")
    if pr.get("disputes_opened"):
        st.markdown("**Dispute / credit cases opened**")
        st.dataframe([{"#": d.get("dispute_id"), "invoice": d.get("invoice_no"),
                       "amount": _money(d.get("amount")), "reason": d.get("reason_code")}
                      for d in pr["disputes_opened"]], hide_index=True, width="stretch")

    if st.button("↩ Process another payment", type="primary"):
        _reset()
        st.rerun()


# ── entry point ──────────────────────────────────────────────────────────
def render() -> None:
    st.title("💵 Cash Application — Review Queue")
    st.caption("Match incoming payments to open invoices. Clean matches auto-post; "
               "the long tail pauses for human approval — the AI recommends, a person decides.")
    _ensure_seeded()
    st.session_state.setdefault("phase", "idle")

    _sidebar()

    phase = st.session_state["phase"]
    if phase == "paused":
        _panel_review()
    elif phase == "done":
        _panel_result()
    else:
        _panel_submit()


if __name__ == "__main__":
    render()
