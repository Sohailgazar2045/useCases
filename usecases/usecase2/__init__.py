"""Use Case 2 — Cash Application (Accounts Receivable).

Automates AR cash application: match an incoming payment/remittance to the right
open invoice(s) in the mock D365 (SQLite) and post it — auto-posting clean exact
matches and routing the messy long tail (short-pay, no-reference, multi-invoice,
exceptions) to a human via LangGraph interrupt/resume.

Built with Python + OpenAI + LangGraph. See plan/UC2_EXECUTION_PLAN.md.
"""
