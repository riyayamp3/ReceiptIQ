"""Sarvam Vision Document AI.

extract():  image -> structured receipts via a JSON schema (what the app uses; handles handwriting, Indic
            scripts and several receipts in one photo, with per-field confidence).
digitise(): image -> raw text lines (used by the evaluation to compare OCR engines with the same parser).
"""
import io
import json
import os
import re
import time
from datetime import date
from pathlib import Path

from PIL import Image
from sarvamai import SarvamAI
from sarvamai.core.api_error import ApiError

import gst

DONE = {"completed", "partially_completed"}
TERMINAL = DONE | {"failed", "rejected"}
LANG_CODES = {"en-IN": "en", "hi-IN": "hi", "mr-IN": "mr"}
CATEGORIES = ["Food & Dining", "Groceries", "Shopping", "Travel", "Bills & Utilities", "Entertainment", "Health", "Other"]


def _num(description):
    return {"type": "number", "description": description}


# Sarvam allows at most 4 nesting levels, so items sit in a flat list pointing at their receipt
SCHEMA = {"type": "object", "properties": {
    "receipts": {
        "type": "array",
        "description": "One entry per separate receipt or bill in the image. A photo can show several receipts side by side; never merge them.",
        "items": {"type": "object", "description": "One receipt", "properties": {
            "merchant": {"type": "string", "description": "Business name only, e.g. 'IndianOil' or 'Shree Chaats'. Not the customer, and without greetings like 'Welcome', slogans or the same name repeated in another script."},
            "date": {"type": "string", "description": "Bill date as YYYY-MM-DD. Indian receipts write dates day-first (DD/MM/YY)."},
            "subtotal": _num("Amount before tax, if printed"),
            "tax": _num("Total tax in rupees (CGST + SGST + IGST or VAT), not the percentage"),
            "cgst": _num("CGST amount in rupees, if shown"),
            "sgst": _num("SGST (or UTGST) amount in rupees, if shown"),
            "igst": _num("IGST amount in rupees, if shown"),
            "gst_rate": _num("Combined GST rate in percent as printed: CGST% + SGST%, or IGST%. E.g. CGST 2.5% + SGST 2.5% = 5"),
            "gstin": {"type": "string", "description": "The seller's 15-character GSTIN / GST number, if printed (not the customer's)"},
            "service_type": {"type": "string", "enum": list(gst.SERVICES),
                             "description": "What was bought, for GST: " + "; ".join(f"{k} = {v[0]}" for k, v in gst.SERVICES.items())},
            "total": _num("Final amount paid after all taxes, discounts, rounding and handwritten additions or deductions"),
            "payment_method": {"type": "string", "enum": ["UPI", "Card", "Cash", "Unknown"], "description": "How the bill was paid"},
            "category": {"type": "string", "enum": CATEGORIES, "description": "Best spending category for this bill"},
        }},
    },
    "items": {
        "type": "array",
        "description": "Every purchased good or service on every receipt, excluding tax, total and adjustment lines",
        "items": {"type": "object", "description": "One purchased item", "properties": {
            "receipt": {"type": "integer", "description": "Which receipt this item is on: 1 for the first entry in receipts, 2 for the second"},
            "name": {"type": "string", "description": "Item name without serial numbers"},
            "qty": _num("Quantity, 1 if not shown"),
            "amount": _num("Line amount in rupees (qty x rate)"),
        }},
    },
}}
FIELDS = ["merchant", "date", "subtotal", "tax", "total", "payment_method", "category",
          "cgst", "sgst", "igst", "gst_rate", "gstin", "service_type"]


def available():
    return bool(os.getenv("SARVAM_API_KEY"))


def client():
    return SarvamAI(api_subscription_key=os.environ["SARVAM_API_KEY"])


def retry(fn, *args, **kwargs):
    """The API allows ~10 requests/minute: wait and retry when rate-limited."""
    for attempt in range(4):
        try:
            return fn(*args, **kwargs)
        except ApiError as e:
            if e.status_code != 429 or attempt == 3:
                raise
            time.sleep(10 * (attempt + 1))


def image_payload(path):
    """Sarvam takes only JPEG/PNG. Web images are often WebP, sometimes even named .jpg, so check the real format."""
    with Image.open(path) as im:
        if im.format in ("JPEG", "PNG"):
            return Path(path).name, Path(path).read_bytes(), f"image/{im.format.lower()}"
        buf = io.BytesIO()
        im.convert("RGB").save(buf, "PNG")
        return Path(path).stem + ".png", buf.getvalue(), "image/png"


def run_job(start, timeout=180):
    """Start a Document AI job, poll until it finishes, return its results."""
    c = client()
    job = retry(start, c)
    deadline, delay = time.monotonic() + timeout, 3.0
    while (status := retry(c.doc_ai.get_status, job.job_id).status.lower()) not in TERMINAL:
        if time.monotonic() > deadline:
            raise RuntimeError("Sarvam job timed out")
        time.sleep(delay)
        delay = min(delay * 1.5, 10)
    if status not in DONE:
        raise RuntimeError(f"Sarvam job {status}")
    return retry(c.doc_ai.get_results, job.job_id)


def iso_date(value):
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        return None


def extract(path):
    """Returns (receipts, raw result). Each receipt: FIELDS + items + per-field confidence."""
    res = run_job(lambda c: c.doc_ai.extract(file=[image_payload(path)], schema=json.dumps(SCHEMA), output_format="json"))
    result, notes = res.result or {}, res.annotations or {}
    note_list = notes.get("receipts") or []
    receipts = []
    for i, r in enumerate(result.get("receipts") or []):
        note = note_list[i] if i < len(note_list) else {}
        receipts.append({
            **{k: r.get(k) for k in FIELDS},
            "date": iso_date(r.get("date")),
            "payment_method": None if r.get("payment_method") == "Unknown" else r.get("payment_method"),
            "category": r.get("category") if r.get("category") in CATEGORIES else None,
            "service_type": r.get("service_type") if r.get("service_type") in gst.SERVICES else "other",
            "confidence": {k: v["confidence"] for k, v in note.items() if isinstance(v, dict) and "confidence" in v},
            "items": [
                {"name": it.get("name") or "", "qty": it.get("qty") or 1, "price": it["amount"]}
                for it in result.get("items") or []
                if (it.get("receipt") or 1) == i + 1 and it.get("amount") is not None
            ],
        })
    return receipts, result


def block_lines(text):
    """One OCR block -> text lines. Table blocks are HTML: one line per <tr>, cells joined by spaces."""
    if "<table" in text:
        text = re.sub(r"</tr>|<br\s*/?>", "\n", re.sub(r"\s*\n\s*", " ", text))
    # handwritten maths comes back as LaTeX: "$2 \times 20 = 40$" -> "2 x 20 = 40"
    text = re.sub(r"\\[a-zA-Z]+|\$", " ", text.replace(r"\times", " x "))
    text = re.sub(r"<[^>]+>|[#*`|]", " ", text).replace("&amp;", "&")
    lines = (" ".join(line.split()) for line in text.splitlines())
    return [line for line in lines if line and not re.fullmatch(r"[\s:\-]*", line)]


def digitise(path):
    """Returns raw text lines in reading order."""
    res = run_job(lambda c: c.doc_ai.digitise(file=[image_payload(path)], output_format="md"))
    # text lives in each page's "blocks" (an extra field the SDK model doesn't declare)
    blocks = [b for doc in res.documents for p in doc.pages or [] for b in getattr(p, "blocks", None) or []]
    blocks.sort(key=lambda b: b.get("reading_order", 0))
    return [line for b in blocks for line in block_lines(b.get("text", ""))]


def detect_language(text):
    """Sarvam text language-ID; anything we don't handle (or no letters) counts as English."""
    text = text[:1000]
    if not re.search(r"[^\W\d_]", text):
        return "en"
    return LANG_CODES.get(retry(client().text.identify_language, input=text).language_code, "en")


if __name__ == "__main__":
    table = "<table>\n  <tbody>\n    <tr>\n      <td>Cafe Latte</td>\n      <td>320</td>\n    </tr>\n    <tr>\n      <td>Total</td><td>500</td>\n    </tr>\n  </tbody>\n</table>"
    assert block_lines(table) == ["Cafe Latte 320", "Total 500"], block_lines(table)
    assert block_lines("# **STARBUCKS**\n\n25/08/26") == ["STARBUCKS", "25/08/26"]
    assert block_lines(r"Masala Chai $2 \times 20 = 40$") == ["Masala Chai 2 x 20 = 40"]
    assert block_lines("Picco &amp; Fall 150") == ["Picco & Fall 150"]
    assert iso_date("2020-12-17") == "2020-12-17" and iso_date("17/12/2020") is None and iso_date(None) is None
    print("sarvam ok")
