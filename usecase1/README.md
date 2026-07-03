# Use Case 1 — Automate Sales Order Entry from Emails and Documents

AI-assisted purchase-order intake for the Customer Experience team: submit a PO
in any common format, extract it with OpenAI, match against ERP master data
(customers + products), review flags, and create a **mock** Dynamics 365 F&O
sales order — with a human approval step before anything is created.

## Supported order formats (per the use-case brief)

| Format | How it's handled |
|--------|------------------|
| **PDF attachment** (typed) | `pdfplumber` extracts text → OpenAI |
| **Typed email body** | pasted text → OpenAI (no attachment needed) |
| **Scanned document** | PDF with no text layer → rendered → OpenAI vision |
| **Handwritten / photo** | image upload (PNG/JPG) → OpenAI vision |

All four use the **same** OpenAI key and the **same** downstream matching /
approval / order-creation pipeline.

## Architecture

```
app.py            Streamlit UI (entry point) — 3-way input selector
extractor.py      PDF/text/image -> OpenAI -> structured JSON
                    extract_order()            (PDF, text or scanned->vision)
                    extract_order_from_text()  (typed email body)
                    extract_order_from_image() (scanned / handwritten image)
matcher.py        fuzzy match vs data/*.json + price validation + flags
order_creator.py  mock D365 sales-order confirmation + persists to orders.json
data/             customers.json, products.json  (hardcoded ERP master data)
orders.json       audit log of created orders (generated at runtime)
```

Two swaps from the original plan for Windows / Python 3.14 robustness:
- **rapidfuzz** instead of fuzzywuzzy + python-Levenshtein (clean wheels, no C build).
- **PyMuPDF** instead of pdf2image for PDF→image (no external Poppler binary).

## Setup

```powershell
pip install -r requirements.txt
copy .env.example .env          # then edit .env and add your real OPENAI_API_KEY
streamlit run app.py
```

`.env`:
```
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o
```

> Extraction requires a valid OpenAI key. Matching and order creation are fully
> local and need no key.

## Matching & edge-case handling

- **Customer:** exact → fuzzy ≥80% (flag: name variation) → fuzzy 50–80% (flag:
  review) → no match (flag: new customer).
- **Product:** exact part number → fuzzy description ≥80% → weak fuzzy (flag) →
  unknown product (flag).
- **Pricing:** matches master → OK; differs → flag with the difference; missing
  or zero → falls back to master price (flagged).

## Demo script

1. **PDF, clean order** → perfect extraction, no flags → **Approve** → mock D365
   order ID, saved to `orders.json`.
2. **Typed email body** → paste an order email → same extraction, no attachment.
3. **Scanned / handwritten image** → upload a photo → vision extraction.
4. **Problem order** (wrong price / unknown product / unknown customer) → show the
   ⚠️ flags that stop bad data before it reaches D365.
5. Explain the mocks: `order_creator.py` (→ `orders.json`) replaces the live D365
   order API, and `data/*.json` replaces live ERP master-data lookups. Only the
   OpenAI key is a real credential in this demo.

## What is mocked vs. production

| Aspect | Demo | Production |
|--------|------|-----------|
| ERP master data | `data/*.json` | live D365 customer / item master |
| Order creation | `orders.json` file | D365 F&O OData API (`SalesOrderHeadersV2`) |
| Email intake | manual paste / upload | automated inbox monitor |
| Extraction | real (OpenAI) | same, + retry / rate-limit handling |
