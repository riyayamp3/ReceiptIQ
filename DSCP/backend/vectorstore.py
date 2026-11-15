"""sqlite-vec vector store operations for receipt embeddings.

Three public functions:
  upsert(receipt_id, family_group_id, vector)  — index or reindex a receipt
  remove(receipt_id)                           — delete the index entry
  retrieve(query_vec, family_group_id, k=8)    — family-scoped KNN, returns [receipt_id, ...]

The ``receipt_embeddings`` virtual table uses a ``family_group_id`` partition key so
that KNN search is physically sharded per family and cannot return results from a
different family regardless of similarity (Req 5.2, 6.5).
"""
import struct
from contextlib import closing

from db import EMBED_DIM, connect


def _vec(vector: list[float]) -> bytes:
    """Serialize a Python float list to the packed binary form sqlite-vec accepts."""
    return struct.pack(f"{len(vector)}f", *vector)


def upsert(receipt_id: int, family_group_id: int, vector: list[float]) -> None:
    """Insert or replace the embedding for a receipt.

    sqlite-vec vec0 tables don't support ON CONFLICT / REPLACE directly, so we
    delete the old row first (if any) and then insert the new one.  This covers
    both the initial confirm (Req 6.2) and re-confirm after an edit (Req 6.3).
    """
    blob = _vec(vector)
    with closing(connect()) as con, con:
        con.execute(
            "delete from receipt_embeddings where receipt_id = ?",
            (receipt_id,),
        )
        con.execute(
            "insert into receipt_embeddings (receipt_id, family_group_id, embedding) values (?, ?, ?)",
            (receipt_id, family_group_id, blob),
        )


def remove(receipt_id: int) -> None:
    """Remove the embedding row for a receipt (called before the receipt row is deleted, Req 6.4)."""
    with closing(connect()) as con, con:
        con.execute(
            "delete from receipt_embeddings where receipt_id = ?",
            (receipt_id,),
        )


def retrieve(query_vec: list[float], family_group_id: int, k: int = 8) -> list[int]:
    """Family-scoped KNN retrieval.

    Returns up to *k* receipt IDs ordered by ascending embedding distance
    (most similar first).  The ``family_group_id = ?`` partition-key equality
    constraint ensures that no receipt from another family is ever considered,
    regardless of its similarity to the query (Req 5.2, 6.5).
    """
    blob = _vec(query_vec)
    with closing(connect()) as con:
        rows = con.execute(
            """select receipt_id, distance
               from receipt_embeddings
               where embedding match ?
                 and k = ?
                 and family_group_id = ?
               order by distance""",
            (blob, k, family_group_id),
        ).fetchall()
    return [row["receipt_id"] for row in rows]
