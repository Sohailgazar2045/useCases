# Flow Report — Use Case 1: Automated Sales Order Entry

**Department:** Customer Experience (CX)
**System:** AI-assisted PO intake → Dynamics 365 F&O (demo / proof-of-concept)
**Scope of this demo:** real OpenAI extraction; ERP master data, D365 order
creation, and email intake are mocked.

---

## 1. End-to-End Flow (at a glance)

```
  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
  │ 1 INTAKE │ → │ 2 EXTRACT│ → │ 3 MATCH  │ → │ 4 REVIEW │ → │ 5 CREATE │
  │ PO in    │   │ AI reads │   │ vs ERP   │   │ human    │   │ D365 SO  │
  │ any form │   │ the data │   │ master   │   │ approves │   │ + log    │
  └──────────┘   └──────────┘   └──────────┘   └──────────┘   └──────────┘
    customer        OpenAI        matcher +        clerk         order_creator
    / clerk         GPT-4o        data/*.json    (Approve/Edit    → orders.json
                                                  /Reject)
```

**One line:** *Any order format comes in → AI reads it → it's checked against ERP
records → a human approves → a sales order is created and logged.*

---

## 2. Stage-by-Stage Flow

### Stage 1 — Intake (any format)
The CX team receives POs in four formats; the app accepts all four via a single
"Order source" selector.

| Format | Path in the app |
|--------|-----------------|
| PDF attachment (typed) | Upload PDF |
| Typed email body | Paste email text |
| Scanned document | Upload PDF (no text layer) |
| Handwritten / photo | Upload image (PNG/JPG) |

→ *Production:* an automated inbox monitor replaces manual upload/paste.

### Stage 2 — Extract (AI reading)
The raw document is turned into structured data by OpenAI GPT-4o.

```
PDF (typed)      → pdfplumber text  ─┐
Typed email body → pasted text      ─┼─→ OpenAI (text)   ─┐
                                     │                     ├─→ structured JSON:
Scanned PDF      → render → image   ─┐                     │   { customer, po_number,
Handwritten/photo→ image bytes      ─┼─→ OpenAI (vision) ─┘     dates, address,
                                                                 line_items[...] }
```

Output is normalized (types coerced, shape guaranteed) so later stages never
break. Each result is tagged with its `_source` (pdf_text / pdf_scanned /
email_text / image).

### Stage 3 — Match (against ERP master data)
Extracted values (the customer's words) are reconciled against the official ERP
records in `data/customers.json` and `data/products.json`.

```
customer_name  → fuzzy match → customer ID + confidence
part_number    → exact match → product; else description → fuzzy match
unit_price     → compare to master price
```

**Matching rules:**

| Entity | Logic | Outcome |
|--------|-------|---------|
| Customer | exact → fuzzy ≥80% → fuzzy 50–80% → none | confidence + flag if not exact |
| Product | exact part# → description ≥80% → weak → none | matched or "unknown product" |
| Price | ==, ≠, or missing/zero | OK / mismatch(+diff) / use master price |

→ *Production:* the two JSON files are replaced by live D365 master-data lookups.

### Stage 4 — Review (Human in the Loop)
Nothing is created automatically. The clerk sees a two-panel screen:

```
┌─────────────────────┬──────────────────────────────┐
│ ORIGINAL DOCUMENT   │ EXTRACTED & MATCHED DATA      │
│ (PDF / image / text │ customer + confidence badge   │
│  as submitted)      │ line-item table (PO vs master │
│                     │   price, status per line)     │
│                     │ ⚠️ FLAGS (red)                │
│                     │ [✅ Approve] [✏️ Edit] [❌ Reject]│
└─────────────────────┴──────────────────────────────┘
```

Flags surfaced for review include: customer name variation, low-confidence
match, unknown product, price mismatch (with the difference), and missing price
(fell back to master).

### Stage 5 — Create (mock D365) + audit log
On **Approve**, a sales order is generated and **persisted**.

```
create_order() → SO-2026-xxxxx (sequential)
              → append full record to orders.json
              → confirmation shown on screen + in sidebar "Created Orders"
```

Saved record contains: order ID, customer ID/name, PO number, delivery date,
every line item (part, qty, effective price, line total), grand total, timestamp.

→ *Production:* this single function is swapped for a live D365 F&O OData call
(`SalesOrderHeadersV2`).

---

## 3. Decision / Flag Logic (what the human sees)

```
                        ┌─────────────────────────┐
  extracted line item → │ known part number?      │
                        └───────────┬─────────────┘
                          yes       │        no
                   ┌────────────────┘        └────────────────┐
                   ▼                                           ▼
          price == master?                          description matches a
           yes │   no                               product by ≥80%?
        ┌──────┘   └──────┐                          yes │      no
        ▼                 ▼                       ┌───────┘      └───────┐
   ✅ Match         ⚠️ Price mismatch             🟡 Fuzzy match   ❌ Unknown
                     (+ difference)                (flag review)    product (flag)
```

Customer name runs the same idea: exact → 🟡 variation flag → 🟠 review →
🔴 new-customer flag.

---

## 4. Component Map

| Stage | Module | Responsibility |
|-------|--------|----------------|
| 1 Intake / UI | `app.py` | source selector, preview, review screen, actions |
| 2 Extract | `extractor.py` | PDF/text/image → OpenAI → normalized JSON |
| 3 Match | `matcher.py` | customer/product match, price validation, flags |
| 5 Create | `order_creator.py` | mock D365 confirmation + persist to `orders.json` |
| ERP master | `data/*.json` | customers + products (hardcoded stand-in) |

---

## 5. Mock vs. Production

| Aspect | Demo (now) | Production |
|--------|-----------|-----------|
| Email intake | manual upload / paste | automated inbox monitor |
| Extraction | **real** (OpenAI GPT-4o) | same + retry / rate-limit handling |
| ERP master data | `data/*.json` | live D365 customer / item master |
| Order creation | `orders.json` file | D365 F&O OData API |
| Credentials used | **only the OpenAI key** | OpenAI + D365 + mailbox (via secrets vault) |

---

## 6. Requirement Coverage

| Brief question | Coverage |
|----------------|----------|
| Read/extract from diverse formats | ✅ PDF, typed email, scanned, handwritten image |
| Match part numbers & customer vs master | ✅ exact + fuzzy |
| Human-in-the-loop approval | ✅ review screen, Approve/Edit/Reject |
| Edge cases (new customer, ambiguous, pricing) | ✅ flagged (partial-order = future enhancement) |
| Phased rollout plan | 📄 narrative (standard products → medical device later) |
| Medical-device specifics | 📄 narrative (adds lot/serial, UDI, expiry, compliance) |

---

## 7. Known Limitations (demo scope)
- "Edit Fields" button is a stub — approval uses extracted values as-is.
- Multi-page scanned PDFs: vision reads page 1 only.
- No idempotency — approving the same PO twice creates two orders.
- File-based store (`orders.json`) — not concurrency-safe for many users.
- Partial-order "hold" state not yet implemented (unknown lines are flagged).
