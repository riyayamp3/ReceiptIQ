"""Rule-based field extraction: OCR lines -> structured receipt. Baseline for later LLM/Sarvam comparison."""
import re
import unicodedata
from datetime import date

# a number not followed by more digits/dot/comma or a % sign (so "2.5%" is not an amount)
NUM = re.compile(r"(\d[\d,]*(?:\.\d{1,2})?)(?![\d.,]|\s*%)")
MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()
DATE_PATTERNS = [
    (re.compile(r"\b(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})\b"), ("y", "m", "d")),
    (re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})\b"), ("d", "m", "y")),  # Indian receipts are day-first
    (re.compile(r"\b(\d{1,2})[\s\-]*(" + "|".join(MONTHS) + r")[a-z]*[\s\-,']*(\d{2,4})\b", re.I), ("d", "mon", "y")),
    (re.compile(r"\b(" + "|".join(MONTHS) + r")[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})\b", re.I), ("mon", "d", "y")),  # Dec 17, 2020
]
# English, then Marathi (एकूण) and Hindi (कुल / योग) labels
TOTAL_KEYS = ["grand total", "net amount", "net payable", "amount payable", "total amount", "bill amount", "total", "एकूण", "एकुण", "कुल", "योग"]
SUBTOTAL = r"sub\s*-?total|उप\s*(?:योग|कुल|एकूण|एकुण)"
NOT_TOTAL = re.compile(SUBTOTAL + r"|total\s*(qty|quantity|items?|disc)", re.I)
# Devanagari vowel signs aren't \w so \b misfires there: whitespace boundaries for कर (tax)
TAX = re.compile(r"\b(c\s*gst|s\s*gst|i\s*gst|gst|vat|tax)\b(?!\s*in|\s*no)|जीएसटी|(?<!\S)कर(?!\S)", re.I)
SKIP_ITEM = re.compile(
    r"total|tax|gst|vat|date|time|bill|invoice|phone|tel\b|mob|cash|card|upi|change|balance|round|"
    r"discount|saving|qty|amount|rate|table|order|token|thank|visit|www|@|"
    r"एकूण|एकुण|कुल|योग|दिनांक|तारीख|जीएसटी|नकद|रोख|धन्यवाद|फोन",
    re.I,
)
SKIP_MERCHANT = re.compile(r"invoice|receipt|bill|welcome|gstin|cash memo|original|duplicate|बिल|पावती|रसीद|बीजक|स्वागत", re.I)


def letters(s):
    """Letter count that includes Devanagari vowel signs (ा ि ी), which isalpha() rejects."""
    return sum(c.isalpha() or unicodedata.category(c).startswith("M") for c in s)


def amounts(line):
    return [float(n.replace(",", "")) for n in NUM.findall(line)]


def find_date(text):
    for pat, order in DATE_PATTERNS:
        for m in pat.finditer(text):
            parts = dict(zip(order, m.groups()))
            month = MONTHS.index(parts["mon"][:3].lower()) + 1 if "mon" in parts else int(parts["m"])
            year = int(parts["y"]) + (2000 if len(parts["y"]) == 2 else 0)
            try:
                return date(year, month, int(parts["d"])).isoformat()
            except ValueError:
                continue
    return None


def keyword_amount(lines, i):
    """Amount on line i, or on the next line (OCR often splits label and value)."""
    for line in lines[i : i + 2]:
        if a := amounts(line):
            return a[-1]
    return None


def find_total(lines):
    for key in TOTAL_KEYS:
        for i in range(len(lines) - 1, -1, -1):  # grand total is near the bottom
            if key in lines[i].lower() and not NOT_TOTAL.search(lines[i]):
                if (v := keyword_amount(lines, i)) is not None:
                    return v, i
    every = [a for line in lines for a in amounts(line) if not find_date(line)]
    return (max(every), None) if every else (None, None)


def find_tax(lines):
    hits = [(line, keyword_amount(lines, i)) for i, line in enumerate(lines) if TAX.search(line)]
    hits = [(line, v) for line, v in hits if v is not None]
    for line, v in hits:
        if "total" in line.lower():
            return v
    return round(sum(v for _, v in hits), 2) if hits else None


def find_merchant(lines):
    """Index of the first header line that looks like a shop name."""
    for i, line in enumerate(lines[:6]):
        n = letters(line)
        if n >= 3 and n > len(line) / 2 and not SKIP_MERCHANT.search(line):
            return i
    return None


def find_items(lines, start, stop, total):
    items = []
    for line in lines[start:stop]:
        nums = amounts(line)
        if not nums or SKIP_ITEM.search(line) or find_date(line) or letters(line) < 3:
            continue
        price = nums[-1]
        if price <= 0 or (total and price > total):
            continue
        # drop numbers, then leftover standalone operators/currency ("2 x 20 = 40", "Rs.") without eating letters ("Box")
        text = re.sub(r"^\s*\d{1,2}[.)]\s+", "", line)  # serial number "1. VEG MEAL"
        name = re.sub(r"(?:\s+(?:[xX×*@=/₹-]|rs\.?|inr))+\s*$", "", " " + NUM.sub(" ", text), flags=re.I)
        name = " ".join(name.split()).strip(" .:-₹")
        qty = nums[0] if len(nums) >= 2 and nums[0].is_integer() and 0 < nums[0] < 100 else 1
        items.append({"name": name, "qty": qty, "price": price})
    return items


def find_payment(text):
    t = text.lower()
    if re.search(r"upi|gpay|google pay|phonepe|paytm|bhim", t):
        return "UPI"
    if re.search(r"card|visa|mastercard|rupay|debit|credit", t):
        return "Card"
    if re.search(r"\bcash\b(?! memo)|नकद|रोख", t):
        return "Cash"
    return None


def extract(lines):
    text = "\n".join(lines)
    total, total_i = find_total(lines)
    m = find_merchant(lines)
    merchant = None if m is None else lines[m].title() if lines[m].isupper() else lines[m]
    start = 0 if m is None else m + 1
    stop = next((i for i, l in enumerate(lines) if NOT_TOTAL.search(l)), total_i if total_i is not None else len(lines))
    sub_i = next((i for i, l in enumerate(lines) if re.search(SUBTOTAL, l, re.I)), None)
    return {
        "merchant": merchant,
        "date": find_date(text),
        "subtotal": keyword_amount(lines, sub_i) if sub_i is not None else None,
        "tax": find_tax(lines),
        "total": total,
        "payment_method": find_payment(text),
        "items": find_items(lines, start, stop, total),
    }


if __name__ == "__main__":
    r = extract(["STARBUCKS", "25/08/26", "Cafe Latte 320", "Chocolate Muffin 180", "TOTAL 500"])
    assert r["merchant"] == "Starbucks" and r["date"] == "2026-08-25" and r["total"] == 500, r
    assert r["items"] == [{"name": "Cafe Latte", "qty": 1, "price": 320}, {"name": "Chocolate Muffin", "qty": 1, "price": 180}], r

    r = extract([
        "TAX INVOICE", "HOTEL SAGAR", "GSTIN 27ABCDE1234F1Z5", "Date: 03-Sep-2026 Time 20:14",
        "Item Qty Rate Amount", "Paneer Tikka 2 180.00 360.00", "Butter Naan 4 40.00 160.00",
        "Sub Total 520.00", "CGST 2.5% 13.00", "SGST 2.5% 13.00", "Grand Total", "Rs. 546.00", "Paid via GPay",
    ])
    assert r["merchant"] == "Hotel Sagar" and r["date"] == "2026-09-03", r
    assert (r["subtotal"], r["tax"], r["total"], r["payment_method"]) == (520, 26, 546, "UPI"), r
    assert r["items"] == [{"name": "Paneer Tikka", "qty": 2, "price": 360}, {"name": "Butter Naan", "qty": 4, "price": 160}], r
    r = extract([  # Marathi, Devanagari digits
        "श्री गणेश किराणा स्टोअर", "दिनांक: १५/०९/२०२६", "तांदूळ 2 60 120", "साखर 1 45", "उप एकूण 165", "एकूण रक्कम 165", "रोख",
    ])
    assert r["merchant"] == "श्री गणेश किराणा स्टोअर" and r["date"] == "2026-09-15", r
    assert (r["subtotal"], r["total"], r["payment_method"]) == (165, 165, "Cash"), r
    assert r["items"] == [{"name": "तांदूळ", "qty": 2, "price": 120}, {"name": "साखर", "qty": 1, "price": 45}], r

    r = extract(["शर्मा जनरल स्टोर", "तारीख 02-09-2026", "आटा 5 kg 250", "दूध 2 60 120", "कुल 370", "UPI से भुगतान"])  # Hindi
    assert r["merchant"] == "शर्मा जनरल स्टोर" and r["date"] == "2026-09-02" and r["total"] == 370, r
    assert [i["price"] for i in r["items"]] == [250, 120] and r["payment_method"] == "UPI", r
    r = extract(["Sharma Tea Stall", "12/09/2026", "Masala Chai 2 x 20 = 40", "Pizza Box 1 Rs. 30", "Total 70"])  # handwritten style
    assert r["items"] == [{"name": "Masala Chai", "qty": 2, "price": 40}, {"name": "Pizza Box", "qty": 1, "price": 30}], r
    r = extract(["SHREE CHAATS", "Dec 17, 2020 at 07:22 PM", "1. Samosa Chaat 2 0 150.00", "Total 150.00"])
    assert r["date"] == "2020-12-17" and r["items"] == [{"name": "Samosa Chaat", "qty": 2, "price": 150}], r
    print("extract ok")
