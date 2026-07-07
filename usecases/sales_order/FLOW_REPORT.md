# Flow Report — Use Case 1: Automated Sales Order Entry

**Department:** Customer Experience (CX)
**System:** AI-assisted PO intake → Dynamics 365 F&O (demo / proof-of-concept)
**Architecture:** Streamlit UI → **FastAPI** backend → **LangChain**/OpenAI
extraction → matcher → Confidence Engine → mock D365.
**Scope of this demo:** real OpenAI extraction (via LangChain); ERP master data,
D365 order creation, and email intake are mocked.

---

## 1. End-to-End Flow (at a glance)

```
  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
  │ 1 INTAKE │ → │ 2 EXTRACT│ → │ 3 MATCH  │ → │ 4 REVIEW │ → │ 5 CREATE │
  │ PO in    │   │ AI reads │   │ vs ERP + │   │ human    │   │ D365 SO  │
  │ any form │   │ the data │   │ score    │   │ approves │   │ + log    │
  └──────────┘   └──────────┘   └──────────┘   └──────────┘   └──────────┘
    customer     LangChain +    matcher +        clerk         order_creator
    / clerk      OpenAI GPT-4o  pricing.json +  (Approve/Edit    → orders.json
                                Confidence Eng.  /Reject)
          └────── FastAPI backend (sales_order/api/main.py) ──────┘
                    Streamlit UI is an HTTP client over these routes
```

**One line:** *Any order format comes in → AI reads it → it's checked against ERP
records and scored → a human approves → a sales order is created and logged.*
The Streamlit UI calls a FastAPI backend that orchestrates every stage.

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
The raw document is turned into structured data by **LangChain** over OpenAI
GPT-4o. LangChain supplies the prompt template, model abstraction, and
structured-output parsing (the reply is coerced into a typed `PurchaseOrder`
Pydantic model — no hand-rolled JSON parsing).

```
PDF (typed)      → pdfplumber text  ─┐
Typed email body → pasted text      ─┼─→ LangChain ChatOpenAI (text)   ─┐
                                     │                                   ├─→ PurchaseOrder:
Scanned PDF      → render → image   ─┐                                   │   { customer, po_number,
Handwritten/photo→ image bytes      ─┼─→ LangChain ChatOpenAI (vision) ─┘     dates, address,
                                                                             line_items[...] }
```

Output is normalized (types coerced, shape guaranteed) so later stages never
break. Each result is tagged with its `_source` (pdf_text / pdf_scanned /
email_text / image).

### Stage 3 — Match (against ERP master data) + Confidence Engine
Extracted values (the customer's words) are reconciled against the official ERP
records: `data/customers.json`, `data/products.json`, and the separate ERP price
list `data/pricing.json`.

```
customer_name  → fuzzy match → customer ID + confidence
part_number    → exact match → product; else description → fuzzy match
unit_price     → compare to master price (looked up in pricing.json)
                          │
                          ▼
              Confidence Engine → overall score (0-100)
                                + recommendation (approve / review / reject)
                                + partial-order detection
```

**Matching rules:**

| Entity | Logic | Outcome |
|--------|-------|---------|
| Customer | exact → fuzzy ≥80% → fuzzy 50–80% → none | confidence + flag if not exact |
| Product | exact part# → description ≥80% → weak → none | matched or "unknown product" |
| Price | ==, ≠, or missing/zero (from `pricing.json`) | OK / mismatch(+diff) / use master price |
| Quantity | present or missing/zero | OK / flag "confirm with customer" |

→ *Production:* the three JSON files (customers, products, pricing) are replaced
by live D365 master-data and trade-agreement lookups.

### Stage 4 — Review (Human in the Loop) + straight-through
A **100% match** (recommendation `auto_approve`: exact customer, every line
exact, prices match, no flags) is **created automatically** — straight-through
processing, no clerk action. This is toggleable ("⚡ Auto-create on 100% match").
Anything less than perfect still stops for review — the clerk sees a two-panel
screen:

```
┌─────────────────────┬──────────────────────────────┐
│ ORIGINAL DOCUMENT   │ EXTRACTED & MATCHED DATA      │
│ (PDF / image / text │ CONFIDENCE % + AI RECOMMEND.  │
│  as submitted)      │ customer + confidence badge   │
│                     │ line-item table (PO vs master │
│                     │   price, status per line)     │
│                     │ ⚠️ FLAGS (red)                │
│                     │ [✅ Approve] [✏️ Edit] [❌ Reject]│
└─────────────────────┴──────────────────────────────┘
```

The reviewer sees the overall **confidence score** and the engine's
**recommendation** (approve / review / reject) alongside the "why" reasons.
Flags surfaced for review include: customer name variation, low-confidence
match, unknown product, price mismatch (with the difference), missing price
(fell back to master), and missing quantity. For a **partial order** (some lines
match, some don't) the reviewer can create the matched lines and hold the rest.

### Stage 5 — Create (mock D365) + audit log
On **Approve**, a sales order is generated and **persisted**.

```
create_order() → duplicate-PO check (idempotent — returns existing if seen)
              → SO-2026-xxxxx (sequential)
              → append full record to orders.json (optionally partial lines)
              → confirmation shown on screen + in sidebar "Created Orders"
```

Saved record contains: order ID, customer ID/name, PO number, delivery date,
every (selected) line item (part, qty, effective price, line total), grand
total, partial/held-line flags, timestamp.

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
| Orchestration | `api/main.py`, `api/schemas.py` (in this package) | FastAPI `/process`, `/orders`, `/health` |
| 1 Intake / UI | `ui.py` | source selector, preview, review screen, actions (HTTP client) |
| 2 Extract | `extractor.py` | PDF/text/image → LangChain + OpenAI → normalized JSON |
| 3 Match | `matcher.py` | customer/product match, price validation, flags |
| 3 Score | `confidence.py` | overall confidence score, recommendation, partial detection |
| 5 Create | `order_creator.py` | mock D365 confirmation (idempotent) + persist to `orders.json` |
| ERP master | `data/*.json` | customers, products, pricing (hardcoded stand-in) |

---

## 5. Mock vs. Production

| Aspect | Demo (now) | Production |
|--------|-----------|-----------|
| Orchestration | FastAPI backend (`sales_order/api/main.py`) | same API service, fronting D365 |
| Email intake | manual upload / paste | automated inbox monitor |
| Extraction | **real** (LangChain + OpenAI GPT-4o) | same + retry / rate-limit handling |
| ERP master data | `data/*.json` (customers, products, pricing) | live D365 master + trade agreements |
| Order creation | `orders.json` file | D365 F&O OData API |
| Credentials used | **only the OpenAI key** | OpenAI + D365 + mailbox (via secrets vault) |

---

## 6. Requirement Coverage

| Brief question | Coverage |
|----------------|----------|
| Read/extract from diverse formats | ✅ PDF, typed email, scanned, handwritten image |
| Match part numbers & customer vs master | ✅ exact + fuzzy |
| Human-in-the-loop approval | ✅ review screen w/ confidence + AI recommendation |
| Edge cases (new customer, unknown product, pricing, missing qty, duplicate PO, partial order, low-confidence) | ✅ flagged / handled |
| Phased rollout plan | 📄 narrative (standard products → medical device later) |
| Medical-device specifics | 📄 narrative (adds lot/serial, UDI, expiry, compliance) |

---

## 7. Known Limitations (demo scope)
- "Edit Fields" button is a stub — approval uses extracted values as-is.
- Multi-page scanned PDFs: vision reads page 1 only.
- File-based store (`orders.json`) — not concurrency-safe for many users.
- Duplicate-PO idempotency keys on PO number only (no fuzzy/near-duplicate check).
- No auth on the FastAPI backend — demo assumes a trusted local network.

**Resolved since the initial plan:** idempotent duplicate-PO handling, partial-order
"hold" (create matched lines, hold the rest), missing-quantity flagging, and an
order-level Confidence Engine with an AI recommendation are now implemented.
