import json
import re
import tempfile
import uuid
from datetime import date
from contextlib import closing
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Response, UploadFile
from pydantic import BaseModel
from sarvamai.core.api_error import ApiError

import auth
from analysis import analysis
import gst
import sarvam
from db import ROOT, connect, image_mime, migrate
from extract import extract
from insights import budget_status, compare_months, insights
from ocr import run_ocr

load_dotenv(ROOT / ".env")
IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
CATEGORIES = sarvam.CATEGORIES
GST_FIELDS = ["cgst", "sgst", "igst", "gst_rate", "gstin", "service_type"]

migrate()
app = FastAPI(title="ReceiptIQ")
app.include_router(auth.router)
User = Depends(auth.current_user)


class Item(BaseModel):
    name: str
    qty: float = 1
    price: float


class ReceiptIn(BaseModel):
    merchant: str | None = None
    date: str | None = None
    subtotal: float | None = None
    tax: float | None = None
    total: float | None = None
    payment_method: str | None = None
    category: str | None = None
    cgst: float | None = None
    sgst: float | None = None
    igst: float | None = None
    gst_rate: float | None = None
    gstin: str | None = None
    service_type: str | None = None
    status: Literal["pending", "confirmed"] = "pending"
    items: list[Item] = []


def get_receipt(con, rid, uid):
    """A receipt the user owns; someone else's receipt is a 404, same as a missing one."""
    row = con.execute("select * from receipts where id = ? and user_id = ?", (rid, uid)).fetchone()
    if not row:
        raise HTTPException(404, "Receipt not found")
    items = con.execute("select name, qty, price from items where receipt_id = ? order by id", (rid,)).fetchall()
    return {
        **row,
        "field_confidence": json.loads(row["field_confidence"] or "{}"),
        "gst_check": [{"level": lvl, "message": msg} for lvl, msg in gst.check(dict(row))],
        "items": [dict(i) for i in items],
    }


def save_items(con, rid, items):
    con.execute("delete from items where receipt_id = ?", (rid,))
    con.executemany(
        "insert into items (receipt_id, name, qty, price) values (?, ?, ?, ?)",
        [(rid, i["name"], i["qty"], i["price"]) for i in items],
    )


@app.get("/api/categories")
def categories():
    return CATEGORIES


@app.get("/api/service-types")
def service_types():
    return [{"value": k, "label": v[0]} for k, v in gst.SERVICES.items()]


@app.get("/api/images/{name}")
def image(name: str, user=User):
    with closing(connect()) as con:
        row = con.execute("select mime, data from images where name = ? and user_id = ?", (name, user["id"])).fetchone()
    if not row:
        raise HTTPException(404, "Image not found")
    return Response(row["data"], media_type=row["mime"], headers={"Cache-Control": "private, max-age=31536000, immutable"})


def read_receipts(path):
    """Image -> (receipts, raw text, engine, language). One photo may hold several receipts."""
    if sarvam.available():  # handles handwriting, हिंदी/मराठी, multi-receipt photos and GST details
        receipts, raw = sarvam.extract(path)
        names = " ".join(filter(None, [r["merchant"] for r in receipts] + [i["name"] for r in receipts for i in r["items"]]))
        for r in receipts:
            key = [r["confidence"][k] for k in ("merchant", "date", "total") if k in r["confidence"]]
            r["ocr_confidence"] = min(key) if key else None
        return receipts, json.dumps(raw, ensure_ascii=False, indent=1), "sarvam", sarvam.detect_language(names)
    lines, confidence = run_ocr(path)  # offline fallback: printed English only
    return [{**extract(lines), "ocr_confidence": confidence, "confidence": {}}], "\n".join(lines), "easyocr", "en"


@app.post("/api/receipts")
def upload(file: UploadFile, user=User):  # sync def: FastAPI runs the slow OCR in a worker thread
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in IMAGE_TYPES:
        raise HTTPException(400, f"Unsupported file type {suffix or '(none)'}; upload an image")
    data = file.file.read()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:  # OCR libraries want a file path
        tmp.write(data)
    try:
        mime = image_mime(tmp.name)
        receipts, raw, engine, lang = read_receipts(tmp.name)
        if not receipts:
            raise ValueError("No receipt found in this image")
    except Exception as e:  # bad image, Sarvam API error or timeout
        if isinstance(e, ApiError):
            body = e.body if isinstance(e.body, dict) else {}
            detail = body.get("detail") or body.get("error", {}).get("message") or e.body
            e = f"Sarvam {e.status_code}: {detail}"
        raise HTTPException(400 if isinstance(e, ValueError) else 502, f"Could not read receipt: {e}")
    finally:
        Path(tmp.name).unlink()

    name, uid = f"{uuid.uuid4().hex}{suffix}", user["id"]
    with closing(connect()) as con, con:
        con.execute("insert into images (name, user_id, mime, data) values (?, ?, ?, ?)", (name, uid, mime, data))
        ids = []
        for r in receipts:
            ids.append(con.execute(
                """insert into receipts (user_id, image, merchant, date, subtotal, tax, total, payment_method, category,
                                         raw_text, ocr_confidence, field_confidence, engine, lang,
                                         cgst, sgst, igst, gst_rate, gstin, service_type)
                   values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (uid, name, r["merchant"], r["date"], r["subtotal"], r["tax"], r["total"], r["payment_method"],
                 r.get("category"), raw, r["ocr_confidence"], json.dumps(r["confidence"]), engine, lang,
                 *(r.get(k) for k in GST_FIELDS)),
            ).lastrowid)
            save_items(con, ids[-1], r["items"])
        return [get_receipt(con, rid, uid) for rid in ids]


@app.get("/api/receipts")
def list_receipts(status: str | None = None, user=User):
    with closing(connect()) as con:
        q, args = "select id from receipts where user_id = ?", (user["id"],)
        if status:
            q, args = q + " and status = ?", (*args, status)
        return [get_receipt(con, r["id"], user["id"]) for r in con.execute(q + " order by date desc, id desc", args)]


@app.put("/api/receipts/{rid}")
def update_receipt(rid: int, body: ReceiptIn, user=User):
    if body.category and body.category not in CATEGORIES:
        raise HTTPException(400, "Unknown category")
    if body.service_type and body.service_type not in gst.SERVICES:
        raise HTTPException(400, "Unknown service type")
    with closing(connect()) as con, con:
        get_receipt(con, rid, user["id"])  # 404 unless it's theirs
        con.execute(
            """update receipts set merchant = ?, date = ?, subtotal = ?, tax = ?, total = ?, payment_method = ?,
               category = ?, cgst = ?, sgst = ?, igst = ?, gst_rate = ?, gstin = ?, service_type = ?, status = ?
               where id = ?""",
            (body.merchant, body.date, body.subtotal, body.tax, body.total, body.payment_method, body.category,
             *(getattr(body, k) for k in GST_FIELDS), body.status, rid),
        )
        save_items(con, rid, [i.model_dump() for i in body.items])
        return get_receipt(con, rid, user["id"])


@app.delete("/api/receipts/{rid}")
def delete_receipt(rid: int, user=User):
    with closing(connect()) as con, con:
        image = get_receipt(con, rid, user["id"])["image"]
        con.execute("delete from receipts where id = ?", (rid,))
        # a multi-receipt photo stays until its last receipt is deleted
        con.execute("delete from images where name = ? and not exists (select 1 from receipts where image = ?)", (image, image))
    return {"ok": True}


@app.get("/api/analysis")
def explore(category: str | None = None, merchant: str | None = None, month: str | None = None, user=User):
    """Drill-down for the Explore page: any mix of category, merchant and month (YYYY-MM)."""
    if month and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(400, "month must look like 2026-09")
    with closing(connect()) as con:
        return analysis(con, user["id"], category, merchant, month)


@app.get("/api/stats")
def stats(user=User):
    """Dashboard numbers and personal insights over the user's confirmed receipts."""
    with closing(connect()) as con:
        q = lambda sql: [dict(r) for r in con.execute(sql, (user["id"],))]
        where = "from receipts where user_id = ? and status = 'confirmed' and total is not null"
        rows = q(f"select date, total, tax, category, merchant, service_type {where}")
        return {
            "summary": q(f"select coalesce(sum(total), 0) as total, count(*) as count, coalesce(avg(total), 0) as avg, coalesce(sum(tax), 0) as tax {where}")[0],
            "by_category": q(f"select coalesce(category, 'Other') as name, sum(total) as total, count(*) as count {where} group by 1 order by 2 desc"),
            "by_month": q(f"select substr(date, 1, 7) as month, sum(total) as total {where} and date is not null group by 1 order by 1"),
            "by_merchant": q(f"select merchant as name, sum(total) as total, count(*) as visits, avg(total) as avg {where} group by merchant order by 2 desc limit 8"),
            "budget": budget_status([r for r in rows if r["date"]], user["monthly_budget"], date.today()),
            "insights": insights(rows, user["monthly_budget"]),
            "month_compare": compare_months(dated, date.today()) if (dated := [r for r in rows if r["date"]]) else None,
        }
