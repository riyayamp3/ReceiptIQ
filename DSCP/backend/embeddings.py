"""Sarvam EmbeddingGemma 2 — text embeddings for the RAG vector store.

embed(texts)       texts -> list of 768-dim normalized float vectors
receipt_text(r, items) -> the string we embed for a receipt
available()        -> True when SARVAM_API_KEY is set
"""
import math
import os

EMBED_DIM = 768
_MODEL = "embeddinggemma-2"


def available() -> bool:
    """Return True when the Sarvam API key is configured."""
    return bool(os.getenv("SARVAM_API_KEY"))


def receipt_text(r: dict, items: list[dict]) -> str:
    """Build the text that represents a receipt for embedding (Req 6.2).

    Combines merchant, date, category, item names, and the raw OCR text so that
    both structured fields and fuzzy item-level searches work well.
    """
    names = ", ".join(i["name"] for i in items if i.get("name"))
    parts = [
        r.get("merchant"),
        r.get("date"),
        r.get("category"),
        f"items: {names}" if names else None,
        r.get("raw_text"),
    ]
    return " | ".join(p for p in parts if p)


def _normalize(vec: list[float]) -> list[float]:
    """L2-normalize a vector so dot product equals cosine similarity."""
    magnitude = math.sqrt(sum(x * x for x in vec))
    if magnitude == 0.0:
        return vec
    return [x / magnitude for x in vec]


def embed(texts: list[str]) -> list[list[float]]:
    """Call the Sarvam /v2/embeddings endpoint and return normalized 768-dim vectors.

    Handles both the SDK attribute form (``res.embeddings``) and the dict form
    (``res["data"][0]["embedding"]``) so the code survives minor SDK variations.
    """
    from sarvamai import SarvamAI

    client = SarvamAI(api_subscription_key=os.environ["SARVAM_API_KEY"])
    results: list[list[float]] = []

    for text in texts:
        res = client.embeddings(model=_MODEL, input=text)

        # SDK attribute form: res.embeddings is a list of vectors (each already a list)
        if hasattr(res, "embeddings") and res.embeddings is not None:
            vec = res.embeddings[0]
        # Dict / OpenAI-compatible form: res["data"][0]["embedding"]
        elif isinstance(res, dict):
            vec = res["data"][0]["embedding"]
        else:
            raise ValueError(f"Unrecognised embedding response shape: {type(res)}")

        results.append(_normalize(list(vec)))

    return results
