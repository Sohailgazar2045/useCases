# Use Case Suite

A collection of AI-assisted business-automation demos. Use Case 1 runs as a
**FastAPI** backend (extraction + matching + confidence + mock D365) fronted by
a **Streamlit** hub UI. Extraction uses **LangChain** over OpenAI GPT-4o.

Install, then run everything with a **single command** — the UI auto-starts the
FastAPI backend on first load:

```powershell
pip install -r requirements.txt
copy .env.example .env          # then edit .env and add your real OPENAI_API_KEY

streamlit run Home.py           # starts the UI and the backend API together
```

The UI reads the backend URL from `API_BASE_URL` (defaults to
`http://localhost:8000`). If a backend is already running there it's reused;
otherwise the UI launches `uvicorn usecases.sales_order.api.main:app` for you and
shuts it down on exit.

<details><summary>Run the API separately (optional — e.g. for <code>--reload</code> or the Swagger docs)</summary>

```powershell
# Terminal 1 — backend API (docs at http://localhost:8000/docs)
uvicorn usecases.sales_order.api.main:app --reload --port 8000

# Terminal 2 — Streamlit hub UI (detects the running API and reuses it)
streamlit run Home.py
```
</details>

## Structure

```
Home.py                       Landing hub (entry point) — lists the use cases
pages/                        One thin wrapper per use case (Streamlit nav)
  1_Sales_Order_Entry.py        set_page_config → usecases.sales_order.ui.render()
  2_Use_Case_2.py               placeholder
  3_Use_Case_3.py               placeholder
usecases/
  __init__.py                 USE_CASES registry (drives the hub cards)
  sales_order/                Use Case 1 — live (self-contained: pipeline + API + UI)
    api/                        FastAPI backend (orchestration layer)
      main.py                     /process, /orders, /health endpoints
      schemas.py                  Pydantic request/response models
      runtime.py                  auto-start the API from the UI (single-command run)
    ui.py                       Streamlit UI (HTTP client over the API)
    extractor.py                PDF/text/image → LangChain + OpenAI → structured JSON
    matcher.py                  fuzzy match vs data/*.json + price validation
    confidence.py               Confidence Engine — overall score + recommendation
    order_creator.py            mock D365 order (idempotent) + persists to orders.json
    data/                       customers.json, products.json, pricing.json (mock ERP)
    orders.json                 audit log of created orders (runtime)
  usecase2/ui.py              Use Case 2 — placeholder
  usecase3/ui.py              Use Case 3 — placeholder
shared/
  config.py                   load .env once; OPENAI_MODEL, API_BASE_URL, key helpers
requirements.txt
```

### Request flow

```
Streamlit UI  ──HTTP──▶  FastAPI  ──▶  extractor (LangChain+OpenAI)
                          /process ──▶  matcher (customers/products/pricing.json)
                                   ──▶  confidence engine (score + recommendation)
              ──HTTP──▶  /orders  ──▶  order_creator (mock D365, idempotent)
```

`.env` (shared by all use cases):

```
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o
```

## Adding a new use case

1. Create `usecases/<name>/ui.py` with a `def render() -> None:` function
   (plus any supporting modules — `extractor.py`, etc.).
2. Register it in `usecases/__init__.py` (`USE_CASES` list) so it appears on the
   hub landing page.
3. Add a wrapper `pages/N_<Name>.py`:

   ```python
   import streamlit as st
   st.set_page_config(page_title="...", page_icon="🧩", layout="wide")
   from usecases.<name>.ui import render
   render()
   ```

Use shared infrastructure via `from shared.config import get_openai_client, OPENAI_MODEL`.

---

## Use Case 1 — Automate Sales Order Entry from Emails and Documents

Submit a purchase order in any common format, extract it with OpenAI, match
against ERP master data (customers + products), review flags, and create a
**mock** Dynamics 365 F&O sales order — with a human approval step before
anything is created.

### Supported order formats

| Format | How it's handled |
|--------|------------------|
| **PDF attachment** (typed) | `pdfplumber` extracts text → OpenAI |
| **Typed email body** | pasted text → OpenAI (no attachment needed) |
| **Scanned document** | PDF with no text layer → rendered → OpenAI vision |
| **Handwritten / photo** | image upload (PNG/JPG) → OpenAI vision |

All four share the same OpenAI key and the same downstream matching / approval /
order-creation pipeline.

Two swaps from the original plan for Windows / Python 3.14 robustness:
- **rapidfuzz** instead of fuzzywuzzy + python-Levenshtein (clean wheels, no C build).
- **PyMuPDF** instead of pdf2image for PDF→image (no external Poppler binary).

> Extraction requires a valid OpenAI key. Matching and order creation are fully
> local and need no key.

### Matching & edge-case handling

- **Customer:** exact → fuzzy ≥80% (flag: name variation) → fuzzy 50–80% (flag:
  review) → no match (flag: new customer).
- **Product:** exact part number → fuzzy description ≥80% → weak fuzzy (flag) →
  unknown product (flag).
- **Pricing:** looked up in `data/pricing.json` (the ERP price list, kept
  separate from the product master). Matches master → OK; differs → flag with the
  difference; missing or zero → falls back to master price (flagged).
- **Quantity:** missing or zero quantity is flagged for confirmation.
- **Duplicate PO:** approving a PO number that already exists returns the
  existing order instead of creating a second one (idempotent).
- **Partial order:** when some lines match the catalog and some don't, the
  reviewer can create the matched lines and hold the rest.

### Confidence Engine

Every processed order gets an overall **confidence score** (0–100) and an **AI
recommendation** shown to the reviewer:

| Recommendation | When | Action |
|----------------|------|--------|
| ✅ Recommend approve (100% match) | customer + all lines match cleanly, no flags | **auto-created** (straight-through) when "⚡ Auto-create on 100% match" is on; else one-click approve |
| 🟡 Needs review | any flag (variation, fuzzy, price diff, missing qty, unknown line) | stops for human Approve / Edit / Reject |
| 🔴 Recommend reject | nothing usable matched (no valid customer/line) | stops for human review |

Straight-through auto-creation is on by default and can be toggled off in the
sidebar to force manual approval for every order.

### What is mocked vs. production

| Aspect | Demo | Production |
|--------|------|-----------|
| Orchestration | FastAPI backend | same API, fronting D365 |
| ERP master data | `data/*.json` (customers, products, pricing) | live D365 customer / item master + trade agreements |
| Order creation | `orders.json` file | D365 F&O OData API (`SalesOrderHeadersV2`) |
| Email intake | manual paste / upload | automated inbox monitor |
| Extraction | real (LangChain + OpenAI) | same, + retry / rate-limit handling |
