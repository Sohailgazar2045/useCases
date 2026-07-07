"""validate_phase1.py — proves Phase 1 "Done when" end-to-end.

Runs the full foundation flow with NO AI: seed the mock D365, list a customer's
open invoices, record + apply a payment that closes an invoice, and confirm an
audit row was written. Prints a clear PASS/FAIL.

Run:  python -m usecases.usecase2.validate_phase1
"""

from __future__ import annotations

from .data.seed import seed
from .db import (
    apply_payment,
    get_audit_log,
    get_invoice,
    get_open_invoices,
    record_payment,
    write_audit,
)

CUSTOMER = "CUST001"
INVOICE = "INV-1001"   # a $5,000 exact-pay invoice from the seed data


def main() -> None:
    print("== Phase 1 validation " + "=" * 40)

    # 1. Seed the fake D365 fresh.
    seed()

    # 2. List open invoices for the customer.
    invoices = get_open_invoices(CUSTOMER)
    print(f"\nOpen invoices for {CUSTOMER}:")
    for inv in invoices:
        print(f"  {inv['invoice_no']}  balance={inv['balance']:.2f}  status={inv['status']}")

    target = get_invoice(INVOICE)
    assert target is not None, f"seed data missing {INVOICE}"
    pay_amount = target["balance"]

    # 3. Record the incoming payment, then apply it to close the invoice.
    payment_id = record_payment(CUSTOMER, pay_amount, source_doc="validate_phase1")
    updated = apply_payment(INVOICE, pay_amount)
    write_audit(
        "post",
        {
            "payment_id": payment_id,
            "invoice_no": INVOICE,
            "applied": pay_amount,
            "new_balance": updated["balance"],
            "new_status": updated["status"],
        },
    )

    # 4. Verify: invoice closed + audit row written.
    print(f"\nApplied {pay_amount:.2f} to {INVOICE} -> status now '{updated['status']}'")
    audit = get_audit_log()
    print(f"Audit rows written: {len(audit)}")
    if audit:
        print(f"  last: {audit[-1]['stage']} -> {audit[-1]['detail']}")

    closed = updated["status"] == "paid" and updated["balance"] == 0.0
    logged = len(audit) >= 1
    gone_from_open = INVOICE not in {i["invoice_no"] for i in get_open_invoices(CUSTOMER)}

    ok = closed and logged and gone_from_open
    print("\n" + ("[PASS] PHASE 1 PASS" if ok else "[FAIL] PHASE 1 FAIL"))
    print(f"   invoice closed: {closed} | audit written: {logged} | "
          f"dropped from open list: {gone_from_open}")


if __name__ == "__main__":
    main()
