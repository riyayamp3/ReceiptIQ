"""Field-level accuracy of receipt extraction methods against hand-labelled ground truth.

Methods:
  rules   Sarvam digitise (OCR text) -> our regex parser (backend/extract.py)
  sarvam  Sarvam extract (image + JSON schema -> fields)

Usage: python eval/evaluate.py            (API results are cached in eval/cache/, delete to re-run)
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / "backend" / ".env")
import extract as rules  # noqa: E402
import sarvam  # noqa: E402

CACHE = HERE / "cache"
FIELDS = ["receipts found", "merchant", "date", "total", "tax", "items"]


def cached(key, fn):
    CACHE.mkdir(exist_ok=True)
    f = CACHE / f"{key}.json"
    if not f.exists():
        f.write_text(json.dumps(fn(), ensure_ascii=False, indent=1), encoding="utf-8")
    return json.loads(f.read_text(encoding="utf-8"))


def predict(method, image):
    path = str(ROOT / image)
    if method == "rules":
        return [rules.extract(cached(f"digitise_{image}", lambda: sarvam.digitise(path)))]
    return cached(f"extract_{image}", lambda: sarvam.extract(path)[0])


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def close(a, b):
    return a is not None and b is not None and abs(float(a) - float(b)) < 0.5


def score(pred, label):
    """Per-field correctness for one labelled receipt (None = field not labelled)."""
    p = pred or {}
    return {
        "merchant": bool(p.get("merchant")) and any(norm(m) in norm(p["merchant"]) for m in label["merchant"]),
        "date": p.get("date") == label["date"],
        "total": close(p.get("total"), label["total"]),
        "tax": close(p.get("tax"), label["tax"]) if "tax" in label else None,
        # every item amount found, nothing extra
        "items": Counter(round(i["price"], 2) for i in p.get("items") or []) == Counter(round(x, 2) for x in label["items"]),
    }


def main():
    labels = {k: v for k, v in json.loads((HERE / "labels.json").read_text(encoding="utf-8")).items() if not k.startswith("_")}
    tally = {}  # (method, group) -> field -> [correct, total]
    for method in ["rules", "sarvam"]:
        for image, spec in labels.items():
            preds = sorted(predict(method, image), key=lambda r: r.get("date") or "")
            gold = sorted(spec["receipts"], key=lambda r: r["date"])
            for group in (spec["group"], "all"):
                t = tally.setdefault((method, group), {f: [0, 0] for f in FIELDS})
                t["receipts found"][0] += len(preds) == len(gold)
                t["receipts found"][1] += 1
                for i, label in enumerate(gold):  # pair in date order; missing predictions score as wrong
                    for field, ok in score(preds[i] if i < len(preds) else None, label).items():
                        if ok is not None:
                            t[field][0] += ok
                            t[field][1] += 1
            if method == "sarvam":
                for i, label in enumerate(gold):
                    wrong = [f for f, ok in score(preds[i] if i < len(preds) else None, label).items() if ok is False]
                    if wrong:
                        print(f"  sarvam miss  {image} #{i + 1}: {', '.join(wrong)}")

    groups = ["handwritten", "printed", "all"]
    print(f"\n{'field':<16}" + "".join(f"{m + ' / ' + g:>24}" for m in ["rules", "sarvam"] for g in groups))
    for field in FIELDS:
        row = ""
        for m in ["rules", "sarvam"]:
            for g in groups:
                ok, n = tally[(m, g)][field]
                row += f"{f'{ok}/{n} ({ok / n:.0%})' if n else '-':>24}"
        print(f"{field:<16}{row}")


if __name__ == "__main__":
    main()
