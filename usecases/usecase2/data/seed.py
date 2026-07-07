"""seed.py — synthetic data seeding for the mock D365 (SQLite).

All data is synthetic. Rows are engineered so the demo hits every match path:
exact, short-pay, overpay, no-reference allocation, multi-invoice, and exception.
Run:  python -m usecases.usecase2.data.seed
"""

from __future__ import annotations

import json

from ..db import DB_PATH, _connect, init_db

CUSTOMERS = [
    ("CUST001", "Northwind Traders"),
    ("CUST002", "Contoso Ltd"),
    ("CUST003", "Fabrikam Inc"),
]

# (invoice_no, customer_id, amount, balance, status, issued_date)
# Designed to exercise each situation the matching engine classifies:
INVOICES = [
    # CUST001 — exact + short-pay targets
    ("INV-1001", "CUST001", 5000.00, 5000.00, "open", "2026-05-01"),   # exact-pay
    ("INV-1002", "CUST001", 3200.00, 3200.00, "open", "2026-05-04"),   # short-pay
    # CUST002 — overpay + no-reference allocation (2500 == 1000 + 1500)
    ("INV-2001", "CUST002", 750.00, 750.00, "open", "2026-05-06"),     # overpay
    ("INV-2002", "CUST002", 1000.00, 1000.00, "open", "2026-05-08"),   # part of no-ref subset
    ("INV-2003", "CUST002", 1500.00, 1500.00, "open", "2026-05-09"),   # part of no-ref subset
    # CUST003 — multi-invoice (referenced) 137000 == 5 lines
    ("INV-3001", "CUST003", 40000.00, 40000.00, "open", "2026-05-10"),
    ("INV-3002", "CUST003", 35000.00, 35000.00, "open", "2026-05-11"),
    ("INV-3003", "CUST003", 30000.00, 30000.00, "open", "2026-05-12"),
    ("INV-3004", "CUST003", 20000.00, 20000.00, "open", "2026-05-13"),
    ("INV-3005", "CUST003", 12000.00, 12000.00, "open", "2026-05-14"),
]


def seed() -> None:
    init_db()
    with _connect() as conn:
        # Reset every table so each seed is a clean, repeatable starting point.
        conn.execute("DELETE FROM disputes")
        conn.execute("DELETE FROM audit_log")
        conn.execute("DELETE FROM payments")
        conn.execute("DELETE FROM open_invoices")
        conn.execute("DELETE FROM customers")
        conn.executemany("INSERT INTO customers VALUES (?, ?)", CUSTOMERS)
        conn.executemany(
            "INSERT INTO open_invoices "
            "(invoice_no, customer_id, amount, balance, status, issued_date, line_items) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(*row, json.dumps([])) for row in INVOICES],
        )
    print(f"Seeded {len(CUSTOMERS)} customers and {len(INVOICES)} invoices into {DB_PATH}")


if __name__ == "__main__":
    seed()
