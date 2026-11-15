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
import chat as chat_module
import embeddings as _embeddings
import groups as groups_module
from analysis import analysis
import gst
import sarvam
import vectorstore
from db import ROOT, connect, image_mime, migrate
from extract import extract
from insights import budget_status, compare_months, insights
from ocr import run_ocr
from passbook import parse_pdf

load_dotenv(ROOT / ".env")
IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
CATEGORIES = sarvam.CATEGORIES
GST_FIELDS = ["cgst", "sgst", "igst", "gst_rate", "gstin", "service_type"]

migrate()
app = FastAPI(title="ReceiptIQ")
app.include_router(auth.router)
app.include_router(groups_module.router)
User = Depends(auth.current_user)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

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
    member: int | None = None          # family member who made the purchase


class ChatIn(BaseModel):
    message: str
    history: list[dict] = []
    member_id: int | None = None
    provider: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fg_scope(user, member=None):
    """Return (WHERE clause, args) scoped to the user's family group (confirmed receipts)."""
    q = "from receipts where family_group_id = ? and status = 'confirmed'"
    args = [user["active_group_id"]]
    if member is not None:
        q += " and member_id = ?"
        args.append(member)
    return q, args


def get_receipt(con, rid, user):
    """Return a receipt that belongs to the user's family group; 404 otherwise."""
    row = con.execute(
        "select * from receipts where id = ? and family_group_id = ?",
        (rid, user["active_group_id"]),
    ).fetchone()
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


def _upsert_embedding(rid: int, fgid: int, receipt: dict) -> None:
    """Generate and store an embedding for a confirmed receipt (non-fatal if unavailable)."""
    if not _embeddings.available():
        return
    try:
        text = _embeddings.receipt_text(receipt, receipt.get("items", []))
        vec = _embeddings.embed([text])[0]
        vectorstore.upsert(rid, fgid, vec)
    except Exception:
        pass  # embedding failures never block the main request


# ---------------------------------------------------------------------------
# Static endpoints
# ---------------------------------------------------------------------------

@app.get("/api/categories")
def categories():
    return CATEGORIES


@app.get("/api/service-types")
def service_types():
    return [{"value": k, "label": v[0]} for k, v in gst.SERVICES.items()]


@app.get("/api/providers")
def available_providers(user=User):
    """Return the ordered list of configured LLM providers (for the Chat UI dropdown)."""
    try:
        return chat_module.providers()
    except HTTPException:
        return []


# ---------------------------------------------------------------------------
# Image endpoint (family-scoped)
# ---------------------------------------------------------------------------

@app.get("/api/images/{name}")
def image(name: str, user=User):
    with closing(connect()) as con:
        row = con.execute(
            """select i.mime, i.data from images i
               where i.name = ?
               and exists (
                   select 1 from receipts r
                   where r.image = i.name and r.family_group_id = ?
               )""",
            (name, user["active_group_id"]),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Image not found")
    return Response(row["data"], media_type=row["mime"],
                    headers={"Cache-Control": "private, max-age=31536000, immutable"})


# ---------------------------------------------------------------------------
# Receipt image upload
# ---------------------------------------------------------------------------

def read_receipts(path):
    """Image -> (receipts, raw text, engine, language)."""
    if sarvam.available():
        receipts, raw = sarvam.extract(path)
        names = " ".join(filter(None, [r["merchant"] for r in receipts] + [i["name"] for r in receipts for i in r["items"]]))
        for r in receipts:
            key = [r["confidence"][k] for k in ("merchant", "date", "total") if k in r["confidence"]]
            r["ocr_confidence"] = min(key) if key else None
        return receipts, json.dumps(raw, ensure_ascii=False, indent=1), "sarvam", sarvam.detect_language(names)
    lines, confidence = run_ocr(path)
    return [{**extract(lines), "ocr_confidence": confidence, "confidence": {}}], "\n".join(lines), "easyocr", "en"


@app.post("/api/receipts")
def upload(file: UploadFile, user=User):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in IMAGE_TYPES:
        raise HTTPException(400, f"Unsupported file type {suffix or '(none)'}; upload an image")
    data = file.file.read()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
    try:
        mime = image_mime(tmp.name)
        receipts, raw, engine, lang = read_receipts(tmp.name)
        if not receipts:
            raise ValueError("No receipt found in this image")
    except Exception as e:
        if isinstance(e, ApiError):
            body = e.body if isinstance(e.body, dict) else {}
            detail = body.get("detail") or body.get("error", {}).get("message") or e.body
            e = f"Sarvam {e.status_code}: {detail}"
        raise HTTPException(400 if isinstance(e, ValueError) else 502, f"Could not read receipt: {e}")
    finally:
        Path(tmp.name).unlink()

    name, uid, fgid = f"{uuid.uuid4().hex}{suffix}", user["id"], user["active_group_id"]
    with closing(connect()) as con, con:
        con.execute("insert into images (name, user_id, mime, data) values (?, ?, ?, ?)", (name, uid, mime, data))
        ids = []
        for r in receipts:
            ids.append(con.execute(
                """insert into receipts (user_id, family_group_id, image, merchant, date, subtotal, tax, total,
                                         payment_method, category, raw_text, ocr_confidence, field_confidence,
                                         engine, lang, cgst, sgst, igst, gst_rate, gstin, service_type)
                   values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (uid, fgid, name, r["merchant"], r["date"], r["subtotal"], r["tax"], r["total"],
                 r["payment_method"], r.get("category"), raw, r["ocr_confidence"],
                 json.dumps(r["confidence"]), engine, lang, *(r.get(k) for k in GST_FIELDS)),
            ).lastrowid)
            save_items(con, ids[-1], r["items"])
        return [get_receipt(con, rid, user) for rid in ids]


# ---------------------------------------------------------------------------
# Passbook PDF upload
# ---------------------------------------------------------------------------

@app.post("/api/passbook")
def upload_passbook(file: UploadFile, user=User):
    """Parse a bank passbook or account statement PDF and create pending receipts."""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix != ".pdf":
        raise HTTPException(400, "Please upload a PDF file (bank statement / passbook export).")
    data = file.file.read()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(data)
    try:
        transactions = parse_pdf(tmp.name)
    except Exception as e:
        raise HTTPException(400, f"Could not parse PDF: {e}")
    finally:
        Path(tmp.name).unlink()

    if not transactions:
        raise HTTPException(400, "No transactions found in this PDF. Make sure it is a bank statement.")

    uid, fgid = user["id"], user["active_group_id"]
    created = []
    with closing(connect()) as con, con:
        for tx in transactions:
            rid = con.execute(
                """insert into receipts (user_id, family_group_id, merchant, date, total,
                                         payment_method, category, raw_text, engine, lang,
                                         field_confidence, status)
                   values (?, ?, ?, ?, ?, ?, ?, ?, 'passbook', 'en', '{}', 'pending')""",
                (uid, fgid,
                 tx["merchant"], tx["date"], tx["total"],
                 tx["payment_method"], tx["category"],
                 tx["description"]),
            ).lastrowid
            created.append(get_receipt(con, rid, user))
    return created


# ---------------------------------------------------------------------------
# Receipt CRUD
# ---------------------------------------------------------------------------

@app.get("/api/receipts")
def list_receipts(status: str | None = None, member: int | None = None, user=User):
    with closing(connect()) as con:
        q, args = "select id from receipts where family_group_id = ?", [user["active_group_id"]]
        if member is not None:
            q += " and member_id = ?"
            args.append(member)
        if status:
            q += " and status = ?"
            args.append(status)
        return [get_receipt(con, r["id"], user) for r in con.execute(q + " order by date desc, id desc", args)]


@app.put("/api/receipts/{rid}")
def update_receipt(rid: int, body: ReceiptIn, user=User):
    if body.category and body.category not in CATEGORIES:
        raise HTTPException(400, "Unknown category")
    if body.service_type and body.service_type not in gst.SERVICES:
        raise HTTPException(400, "Unknown service type")
    with closing(connect()) as con, con:
        get_receipt(con, rid, user)   # 404 unless it's in the user's family
        # Determine member label: use explicit body.member, else keep existing, else default to confirmer
        label = body.member or user["id"]
        member_set = ""
        member_args: list = []
        if body.status == "confirmed":
            member_set = ", member_id = ?"
            member_args = [label]
        con.execute(
            f"""update receipts set merchant = ?, date = ?, subtotal = ?, tax = ?, total = ?,
               payment_method = ?, category = ?, cgst = ?, sgst = ?, igst = ?, gst_rate = ?,
               gstin = ?, service_type = ?, status = ?{member_set}
               where id = ?""",
            (body.merchant, body.date, body.subtotal, body.tax, body.total,
             body.payment_method, body.category,
             *(getattr(body, k) for k in GST_FIELDS), body.status, *member_args, rid),
        )
        save_items(con, rid, [i.model_dump() for i in body.items])
        receipt = get_receipt(con, rid, user)

    # Embed after the transaction commits (non-blocking, best-effort)
    if body.status == "confirmed":
        _upsert_embedding(rid, user["active_group_id"], receipt)
    return receipt


@app.delete("/api/receipts/{rid}")
def delete_receipt(rid: int, user=User):
    with closing(connect()) as con, con:
        image = get_receipt(con, rid, user)["image"]
        vectorstore.remove(rid)   # remove embedding before row deletion
        con.execute("delete from receipts where id = ?", (rid,))
        con.execute(
            "delete from images where name = ? and not exists (select 1 from receipts where image = ?)",
            (image, image),
        )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Analysis & stats (family-scoped)
# ---------------------------------------------------------------------------

@app.get("/api/analysis")
def explore(category: str | None = None, merchant: str | None = None,
            month: str | None = None, member: int | None = None, user=User):
    """Drill-down for the Explore page."""
    if month and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(400, "month must look like 2026-09")
    with closing(connect()) as con:
        return analysis(con, user["active_group_id"], category, merchant, month, member_filter=member)


@app.get("/api/stats")
def stats(member: int | None = None, user=User):
    """Dashboard numbers — family-wide (or one member when ?member= is set)."""
    fgid = user["active_group_id"]
    with closing(connect()) as con:
        where, args = fg_scope(user, member)
        q = lambda sql: [dict(r) for r in con.execute(sql, args)]
        rows = q(f"select date, total, tax, category, merchant, service_type {where}")
        dated = [r for r in rows if r["date"]]
        return {
            "summary": q(
                f"select coalesce(sum(total),0) as total, count(*) as count, "
                f"coalesce(avg(total),0) as avg, coalesce(sum(tax),0) as tax {where}"
            )[0],
            "by_category": q(
                f"select coalesce(category,'Other') as name, sum(total) as total, "
                f"count(*) as count {where} group by 1 order by 2 desc"
            ),
            "by_month": q(
                f"select substr(date,1,7) as month, sum(total) as total {where} "
                f"and date is not null group by 1 order by 1"
            ),
            "by_merchant": q(
                f"select merchant as name, sum(total) as total, count(*) as visits, "
                f"avg(total) as avg {where} group by merchant order by 2 desc limit 8"
            ),
            "budget": budget_status(dated, user["monthly_budget"], date.today()),
            "insights": insights(rows, user["monthly_budget"]),
            "month_compare": compare_months(dated, date.today()) if dated else None,
        }


# ---------------------------------------------------------------------------
# Chat endpoint
# ---------------------------------------------------------------------------

@app.post("/api/chat")
def chat(body: ChatIn, user=User):
    """RAG chatbot — hybrid tool-calling + semantic search over receipts."""
    if not user.get("active_group_id"):
        return {
            "answer": "Please join or create a family group before using the chatbot.",
            "member": {"id": user["id"], "name": user["name"]},
            "family_group_id": None,
            "receipts": [],
            "provider_used": None,
        }
    return chat_module.answer(
        user=user,
        message=body.message,
        history=body.history,
        member_filter=body.member_id,
        preferred_provider=body.provider,
    )
