# ReceiptIQ

Receipt photos → OCR → structured expenses → analytics → (soon) AI assistant.

## Run

```bash
# backend (http://127.0.0.1:8000)
cd backend
pip install -r requirements.txt
python -m uvicorn app:app --reload

# frontend (http://localhost:5173, proxies /api to the backend)
cd frontend
npm install
npm run dev
```

### How receipts are read

Upload any receipt photo (JPEG/PNG/WebP); nothing to choose. With `SARVAM_API_KEY` in `backend/.env`
(template: `.env.example`), **Sarvam Vision Extract** turns the image straight into fields using the JSON schema in
`backend/sarvam.py`: printed or handwritten, English/हिंदी/मराठी, several receipts in one photo. Every field comes
with a confidence score; fields under 0.9 are highlighted on the Review screen.
Without a key the app falls back to **EasyOCR + regex rules** (offline, printed English only).

### GST

Sarvam also extracts CGST / SGST / IGST, the printed rate and the seller's GSTIN, and classifies the bill into a
service type. `backend/gst.py` holds the expected rate for each service **by date** (pre-GST before July 2017,
the old slabs, and the Sept 2025 reform) and checks every receipt: rate matches the service, CGST = SGST,
not IGST and CGST together, GSTIN format. Verdicts show on the Review screen and refresh on save.
The rate table is hand-maintained: check it against CBIC notifications when rates change.

### Accounts & personalisation

Sign up / log in (email + password, salted scrypt hashes, 30-day httpOnly session cookie). Every receipt and
photo belongs to one user and every query checks ownership. Receipts that existed before accounts were added go
to the first account created. The profile holds a monthly budget; the dashboard shows budget progress with a
month-end projection and personal insights computed from your own confirmed receipts (`backend/insights.py`).

### Explore (drill-down analysis)

Click any month, category, merchant or "what changed" bar on the dashboard to open **Explore** for that slice
(`#/explore?category=Groceries&month=2026-09`: back button and bookmarks work). Filters stack, and each page shows
totals and share, insights, month-by-month or day-by-day spend, the weekday pattern, what changed against the
previous month (same days while a month is running), merchants, most-bought items, payment split and the bills.
Habit patterns (weekday, 3-month trend) skip monthly bills (rent, subscriptions) and unusually large one-offs
(above Q3 + 3×IQR of log amounts), both learned from the full history: see `backend/analysis.py`.

### Storage

Everything lives in one SQLite file, `backend/receiptiq.db`: users, sessions, receipts, items, and the original
photos (`images` table), so a receipt and its picture can't get separated.

### Synthetic data

`backend/synth.py` simulates Apr 2025 → today for three personas (student in Pune, professional in Bengaluru,
family in Nagpur): merchants with menus and prices, weekend/payday/Diwali effects, recurring bills, small shops
without GST, correct date-aware GST on every bill (it spans the Sept 2025 reform), and **planted anomalies with
labels** (price spikes, duplicate charges, out-of-profile purchases, ~1.3% each).

```bash
python backend/synth.py --csv data/     # data/transactions.csv + data/items.csv, with ground-truth labels
python backend/synth.py --load          # demo accounts in the app (labels not stored), password demo12345:
                                        # student@ / professional@ / family@demo.receiptiq.in
python backend/synth.py --check         # self-check: reproducible, totals add up, every bill passes gst.py
```

Output depends on the end date (default today); pass `--end 2026-09-28` to reproduce a dataset exactly.

### Evaluation

`python eval/evaluate.py` scores both methods against hand labels in `eval/labels.json`
(API results cached in `eval/cache/`). Current set: 6 images, 7 receipts.

| field | rules (Sarvam OCR + regex) | Sarvam Extract |
|---|---|---|
| receipts found | 83% | 100% |
| merchant | 57% | 100% |
| date | 29% | 100% |
| total | 43% | 100% |
| tax | 50% | 100% |
| item amounts | 14% | 100% |

Self-checks: `python backend/extract.py`, `python backend/sarvam.py`, `python backend/gst.py`, `python backend/insights.py`, `python backend/auth.py`, `python backend/synth.py --check`, `python backend/analysis.py`

## Status

- [x] Part 1: upload, preprocessing, EasyOCR, rule-based extraction, review/correction UI, dashboard
- [x] Part 2a: Sarvam Vision Extract (handwritten, Indic, multi-receipt photos, per-field confidence) + evaluation script
- [x] Synthetic spending generator with labelled anomalies + demo accounts
- [ ] Part 2b: bigger labelled set (SROIE + own receipts), categorisation models
- [x] GST understanding (breakdown, service type, date-aware rate checks); photos stored in the database
- [x] User accounts, profile + monthly budget, personal insights
- [x] Explore: clickable drill-down analysis by category / merchant / month
- [ ] Bank statement PDF import
- [ ] Part 3: anomaly detection, weekly forecasting, insights
- [ ] Part 4: assistant (Sarvam chat + tool calling + embeddings, Saaras voice input)
