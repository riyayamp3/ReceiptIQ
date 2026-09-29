"""Offline baseline OCR: image preprocessing + EasyOCR. Returns receipt text as lines, top to bottom."""
from functools import cache

import cv2
import easyocr
import numpy as np

@cache  # model load takes a few seconds, do it once. English only: Indic/handwritten receipts go to Sarvam
def reader():
    return easyocr.Reader(["en"], gpu=False)


def preprocess(path):
    img = cv2.imread(path)
    if img is None:
        raise ValueError("Could not read image")
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # OCR is weak on small text and slow on huge phone photos: normalise width to 1000-1800px
    w = gray.shape[1]
    scale = min(max(w, 1000), 1800) / w
    if scale != 1:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    gray = cv2.fastNlMeansDenoising(gray, h=10)
    # ponytail: no perspective/skew correction, add when tilted photos hurt line grouping
    return cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)


def group_lines(results):
    """EasyOCR gives word boxes; rebuild receipt rows by vertical overlap."""
    boxes = sorted(
        (min(p[1] for p in box), max(p[1] for p in box), min(p[0] for p in box), text)
        for box, text, _ in results
    )
    rows = []
    for b in boxes:
        mid = (b[0] + b[1]) / 2
        if rows and rows[-1][0][0] <= mid <= rows[-1][0][1]:
            rows[-1].append(b)
        else:
            rows.append([b])
    return [" ".join(b[3] for b in sorted(row, key=lambda b: b[2])) for row in rows]


def run_ocr(path):
    results = reader().readtext(preprocess(path))
    if not results:
        return [], 0.0
    return group_lines(results), float(np.mean([c for *_, c in results]))
