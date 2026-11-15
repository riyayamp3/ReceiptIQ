"""SQLite database: schema, migrations for older files, connection helper. Everything lives in receiptiq.db."""
import secrets
import sqlite3
from contextlib import closing
from pathlib import Path

import sqlite_vec
from PIL import Image

ROOT = Path(__file__).parent
DB = ROOT / "receiptiq.db"

EMBED_DIM = 768

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
  token_hash text primary key,
  user_id integer not null references users(id) on delete cascade,
  expires_at text not null
);
create table if not exists images (
  name text primary key,
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
create table if not exists groups (
  id integer primary key,
  name text not null,
  join_code text not null unique,
  created_by integer references users(id) on delete set null,
  created_at text default current_timestamp
);
create table if not exists group_members (
  group_id integer not null references groups(id) on delete cascade,
  user_id integer not null references users(id) on delete cascade,
  joined_at text default current_timestamp,
  primary key (group_id, user_id)
);
"""

ADDED_COLUMNS = {
    "receipts": {"engine": "text", "lang": "text", "field_confidence": "text", "cgst": "real", "sgst": "real",
                 "igst": "real", "gst_rate": "real", "gstin": "text", "service_type": "text", "user_id": "integer",
                 "family_group_id": "integer", "member_id": "integer"},
    "images": {"user_id": "integer"},
    "users": {"family_group_id": "integer", "active_group_id": "integer"},
}


def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("pragma foreign_keys = on")
    try:
        con.enable_load_extension(True)
        sqlite_vec.load(con)
        con.enable_load_extension(False)
    except (AttributeError, sqlite3.OperationalError):
        pass
    return con


def image_mime(path):
    try:
        with Image.open(path) as im:
            return Image.MIME.get(im.format, "application/octet-stream")
    except OSError:
        raise ValueError("This file is not a readable image")


def migrate():
    old = ROOT / "uploads"
    with closing(connect()) as con, con:
        con.executescript(SCHEMA)
        # Rename old family_groups table to groups if it exists
        try:
            con.execute("alter table family_groups rename to groups")
        except sqlite3.OperationalError:
            pass  # already renamed or never existed
        for table, columns in ADDED_COLUMNS.items():
            have = {r["name"] for r in con.execute(f"pragma table_info({table})")}
            for col, kind in columns.items():
                if col not in have:
                    con.execute(f"alter table {table} add column {col} {kind}")

        # Backfill: move existing family_group_id memberships into group_members
        existing = con.execute(
            "select id, family_group_id from users where family_group_id is not null"
        ).fetchall()
        for u in existing:
            try:
                con.execute(
                    "insert or ignore into group_members (group_id, user_id) values (?, ?)",
                    (u["family_group_id"], u["id"]),
                )
            except sqlite3.IntegrityError:
                pass

        # Backfill: users without active_group_id but with family_group_id
        con.execute("update users set active_group_id = family_group_id where active_group_id is null and family_group_id is not null")

        # Users without any group: create a solo group
        users_without_group = con.execute(
            "select id, name from users where active_group_id is null"
        ).fetchall()
        for u in users_without_group:
            code = secrets.token_urlsafe(4)
            fgid = con.execute(
                "insert into groups (name, join_code, created_by) values (?, ?, ?)",
                (f"{u['name']}'s group", code, u["id"]),
            ).lastrowid
            con.execute("update users set active_group_id = ? where id = ?", (fgid, u["id"]))
            con.execute("insert or ignore into group_members (group_id, user_id) values (?, ?)", (fgid, u["id"]))

        # Backfill receipts.family_group_id from uploader's active_group_id
        con.execute("""
            update receipts
            set family_group_id = (
                select active_group_id from users where users.id = receipts.user_id
            )
            where family_group_id is null
        """)
        con.execute("""
            update receipts set member_id = user_id
            where status = 'confirmed' and member_id is null
        """)

        for f in old.glob("*") if old.exists() else []:
            con.execute("insert or ignore into images (name, mime, data) values (?, ?, ?)", (f.name, image_mime(f), f.read_bytes()))

    if old.exists():
        for f in old.glob("*"):
            f.unlink()
        old.rmdir()

    try:
        with closing(connect()) as con:
            con.execute("""
                create virtual table if not exists receipt_embeddings using vec0(
                    receipt_id      integer primary key,
                    family_group_id integer partition key,
                    embedding       float[768]
                )
            """)
    except Exception:
        pass
