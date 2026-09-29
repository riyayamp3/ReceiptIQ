"""SQLite database: schema, migrations for older files, connection helper. Everything lives in receiptiq.db."""
import sqlite3
from contextlib import closing
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).parent
DB = ROOT / "receiptiq.db"

SCHEMA = """
create table if not exists users (
  id integer primary key,
  name text not null,
  email text not null unique collate nocase,
  password_hash text not null,
  monthly_budget real,
  created_at text default current_timestamp
);
create table if not exists sessions (
  token_hash text primary key,  -- sha256 of the cookie token, so a leaked database can't be used to log in
  user_id integer not null references users(id) on delete cascade,
  expires_at text not null
);
create table if not exists images (
  name text primary key,  -- one photo can hold several receipts, so receipts point here
  user_id integer references users(id) on delete cascade,
  mime text not null,
  data blob not null
);
create table if not exists receipts (
  id integer primary key,
  user_id integer references users(id) on delete cascade,
  created_at text default current_timestamp,
  image text references images(name), merchant text, date text,
  subtotal real, tax real, total real,
  payment_method text, category text,
  raw_text text, ocr_confidence real, field_confidence text, engine text, lang text,
  cgst real, sgst real, igst real, gst_rate real, gstin text, service_type text,
  status text default 'pending' check (status in ('pending', 'confirmed'))
);
create table if not exists items (
  id integer primary key,
  receipt_id integer not null references receipts(id) on delete cascade,
  name text, qty real default 1, price real
);
"""
# columns added after the first release; older databases get them on startup
ADDED_COLUMNS = {
    "receipts": {"engine": "text", "lang": "text", "field_confidence": "text", "cgst": "real", "sgst": "real",
                 "igst": "real", "gst_rate": "real", "gstin": "text", "service_type": "text", "user_id": "integer"},
    "images": {"user_id": "integer"},
}


def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("pragma foreign_keys = on")
    return con


def image_mime(path):
    """Also rejects files that aren't really images."""
    try:
        with Image.open(path) as im:
            return Image.MIME.get(im.format, "application/octet-stream")
    except OSError:
        raise ValueError("This file is not a readable image")


def migrate():
    with closing(connect()) as con, con:
        con.executescript(SCHEMA)
        for table, columns in ADDED_COLUMNS.items():
            have = {r["name"] for r in con.execute(f"pragma table_info({table})")}
            for col, kind in columns.items():
                if col not in have:
                    con.execute(f"alter table {table} add column {col} {kind}")
        # receipt photos used to live in backend/uploads/: move them into the database
        old = ROOT / "uploads"
        for f in old.glob("*") if old.exists() else []:
            con.execute("insert or ignore into images (name, mime, data) values (?, ?, ?)", (f.name, image_mime(f), f.read_bytes()))
    if old.exists():  # only after the copy is committed
        for f in old.glob("*"):
            f.unlink()
        old.rmdir()
