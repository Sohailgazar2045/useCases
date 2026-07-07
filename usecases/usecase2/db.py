"""db.py — Module 1: the mock D365 (SQLite) data-access layer.

Stands in for Dynamics 365 F&O as the system of record. In production this is
the ONLY module that changes — the graph, matching engine, and HITL logic swap
their calls here for the D365 OData / cash-application API. Everything else is
untouched. See UC2_EXECUTION_PLAN.md §7.

Uses the stdlib ``sqlite3`` (no ORM) to keep the demo dependency-light.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    customer_id   TEXT PRIMARY KEY,
    name          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS open_invoices (
    invoice_no    TEXT PRIMARY KEY,
    customer_id   TEXT NOT NULL REFERENCES customers(customer_id),
    amount        REAL NOT NULL,          -- original invoice amount
    balance       REAL NOT NULL,          -- remaining open balance
    status        TEXT NOT NULL DEFAULT 'open',   -- open | partially_paid | paid
    issued_date   TEXT,
    line_items    TEXT                     -- JSON blob (optional)
);

CREATE TABLE IF NOT EXISTS payments (
    payment_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id   TEXT,
    amount        REAL NOT NULL,
    currency      TEXT DEFAULT 'USD',
    received_date TEXT,
    source_doc    TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    stage         TEXT NOT NULL,           -- ingest|extract|match|decision|human|post
    detail        TEXT                     -- JSON blob describing what happened
);

CREATE TABLE IF NOT EXISTS disputes (
    dispute_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_no    TEXT,
    customer_id   TEXT,
    amount        REAL NOT NULL,           -- withheld / disputed amount
    reason_code   TEXT NOT NULL,           -- SHORT_SHIP|DAMAGE|PRICING|TAX|CREDIT|UNKNOWN
    note          TEXT,
    status        TEXT NOT NULL DEFAULT 'open',   -- open | resolved
    created_ts    TEXT NOT NULL
);
"""


def _connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path | str = DB_PATH) -> None:
    """Create the schema if it does not exist."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.executescript(SCHEMA)


# ── Reads ────────────────────────────────────────────────────────────────
def resolve_customer(identifier: str, db_path: Path | str = DB_PATH) -> str | None:
    """Resolve a customer id OR name to the canonical customer_id.

    Remittances usually show a customer *name*; the system of record keys on the
    account *id*. This bridges the two. Returns None if no match.
    """
    if not identifier:
        return None
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT customer_id FROM customers WHERE customer_id = ?", (identifier,)
        ).fetchone()
        if row:
            return row["customer_id"]
        row = conn.execute(
            "SELECT customer_id FROM customers WHERE lower(name) = lower(?)", (identifier,)
        ).fetchone()
        return row["customer_id"] if row else None


def get_open_invoices(customer_id: str, db_path: Path | str = DB_PATH) -> list[dict]:
    """Return all open (non-fully-paid) invoices for a customer."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM open_invoices "
            "WHERE customer_id = ? AND status != 'paid' "
            "ORDER BY issued_date",
            (customer_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_invoice(invoice_no: str, db_path: Path | str = DB_PATH) -> dict | None:
    """Return one invoice by number (any status), or None if it doesn't exist."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM open_invoices WHERE invoice_no = ?", (invoice_no,)
        ).fetchone()
    return dict(row) if row else None


def get_audit_log(db_path: Path | str = DB_PATH) -> list[dict]:
    """Return the full audit trail, oldest first. ``detail`` is parsed back to a dict."""
    import json

    with _connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM audit_log ORDER BY id").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["detail"] = json.loads(d["detail"]) if d["detail"] else {}
        except (json.JSONDecodeError, TypeError):
            pass
        out.append(d)
    return out


# ── Writes ───────────────────────────────────────────────────────────────
def apply_payment(
    invoice_no: str,
    applied_amount: float,
    db_path: Path | str = DB_PATH,
) -> dict:
    """Apply ``applied_amount`` to one invoice, updating its balance and status.

    Returns the updated invoice row as a dict.
    """
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM open_invoices WHERE invoice_no = ?", (invoice_no,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Invoice {invoice_no!r} not found")

        new_balance = round(row["balance"] - applied_amount, 2)
        status = "paid" if new_balance <= 0.005 else "partially_paid"
        conn.execute(
            "UPDATE open_invoices SET balance = ?, status = ? WHERE invoice_no = ?",
            (max(new_balance, 0.0), status, invoice_no),
        )
        updated = conn.execute(
            "SELECT * FROM open_invoices WHERE invoice_no = ?", (invoice_no,)
        ).fetchone()
    return dict(updated)


def record_payment(
    customer_id: str,
    amount: float,
    currency: str = "USD",
    received_date: str | None = None,
    source_doc: str | None = None,
    db_path: Path | str = DB_PATH,
) -> int:
    """Record an incoming payment in the ``payments`` table. Returns its id."""
    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO payments (customer_id, amount, currency, received_date, source_doc) "
            "VALUES (?, ?, ?, ?, ?)",
            (customer_id, amount, currency, received_date, source_doc),
        )
        return int(cur.lastrowid)


def record_dispute(
    invoice_no: str,
    customer_id: str,
    amount: float,
    reason_code: str,
    note: str = "",
    db_path: Path | str = DB_PATH,
) -> int:
    """Open a dispute/credit case for a withheld amount. Returns its id."""
    ts = datetime.now(timezone.utc).isoformat()
    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO disputes (invoice_no, customer_id, amount, reason_code, note, created_ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (invoice_no, customer_id, amount, reason_code, note, ts),
        )
        return int(cur.lastrowid)


def get_disputes(db_path: Path | str = DB_PATH) -> list[dict]:
    """Return all dispute/credit cases, newest first."""
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM disputes ORDER BY dispute_id DESC").fetchall()
    return [dict(r) for r in rows]


def write_audit(stage: str, detail: dict, db_path: Path | str = DB_PATH) -> None:
    """Append an audit-trail row. ``detail`` is stored as JSON."""
    import json

    ts = datetime.now(timezone.utc).isoformat()
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO audit_log (ts, stage, detail) VALUES (?, ?, ?)",
            (ts, stage, json.dumps(detail, default=str)),
        )


if __name__ == "__main__":
    # Phase 1 validation: list open invoices for a customer, apply a payment.
    import sys

    init_db()
    cust = sys.argv[1] if len(sys.argv) > 1 else "CUST001"
    for inv in get_open_invoices(cust):
        print(inv["invoice_no"], inv["balance"], inv["status"])
