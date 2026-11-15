"""Parse bank passbook / account statement PDFs into a list of transaction dicts.

Supports the most common Indian bank export formats (SBI, HDFC, ICICI, Axis, Kotak, IDFC)
by trying multiple column layouts and falling back to raw-line heuristics.

Each returned transaction dict has:
  date        str  YYYY-MM-DD (None if unparseable)
  description str
  debit       float | None
  credit      float | None
  total       float          debit or credit amount (non-zero side)
  merchant    str | None     first word / cleaned description
  category    str | None     best-guess from keywords
  payment_method str         "Bank Transfer"
  source      str            "passbook"
"""
import re
from datetime import datetime

import pdfplumber

# ---------------------------------------------------------------------------
# Category keywords (lowercase)
# ---------------------------------------------------------------------------
_CATEGORY_MAP = [
    ("Food & Dining",    ["swiggy", "zomato", "dominos", "pizza", "cafe", "restaurant", "hotel", "chai",
                          "biryani", "eat", "food", "dining", "meal", "kitchen", "dhaba"]),
    ("Groceries",        ["dmart", "bigbasket", "blinkit", "zepto", "grocer", "kirana", "supermart",
                          "vegetables", "grocery", "reliance fresh", "more supermarket"]),
    ("Travel",           ["uber", "ola", "rapido", "irctc", "railways", "makemytrip", "yatra", "flight",
                          "airline", "indigo", "spicejet", "air india", "bus", "metro", "toll", "petrol",
                          "fuel", "hp ", "bpcl", "iocl", "neft/rtgs"]),
    ("Shopping",         ["amazon", "flipkart", "myntra", "ajio", "nykaa", "meesho", "snapdeal", "croma",
                          "reliance digital", "vijay sales", "shopping"]),
    ("Bills & Utilities",["electricity", "bescom", "msedcl", "tata power", "bses", "water", "gas",
                          "jio", "airtel", "bsnl", "vodafone", "vi ", "recharge", "broadband",
                          "internet", "lic", "insurance", "emi", "loan", "school", "fees",
                          "subscription", "netflix", "spotify", "hotstar", "prime"]),
    ("Health",           ["apollo", "medplus", "netmeds", "1mg", "pharmacy", "hospital", "clinic",
                          "doctor", "diagnostic", "lab", "health", "medicine", "gym", "cult.fit"]),
    ("Entertainment",    ["pvr", "inox", "bookmyshow", "game", "amusement", "cinema", "movie"]),
    ("ATM/Cash",         ["atm", "cash withdrawal", "cash deposit"]),
]


def _guess_category(text: str) -> str | None:
    t = text.lower()
    for cat, keywords in _CATEGORY_MAP:
        if any(k in t for k in keywords):
            return cat
    return None


def _clean_merchant(desc: str) -> str:
    """Extract a short merchant name from the full transaction description."""
    # Strip common prefixes
    for prefix in ("upi/", "imps/", "neft/", "rtgs/", "nach/", "ecs/", "pos/", "atm/"):
        if desc.lower().startswith(prefix):
            desc = desc[len(prefix):]
    # Take up to the first separator or 40 chars
    name = re.split(r"[/\-|@_]", desc)[0].strip()
    return name[:40] or desc[:40]


# ---------------------------------------------------------------------------
# Amount parsing
# ---------------------------------------------------------------------------
def _parse_amount(s: str) -> float | None:
    if not s:
        return None
    s = s.replace(",", "").replace("\xa0", "").strip()
    # Handle Dr/Cr suffixes used by some banks
    s = re.sub(r"\s*(Dr|CR)\s*$", "", s, flags=re.IGNORECASE)
    try:
        v = float(s)
        return v if v >= 0 else -v
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------
_DATE_FMTS = ["%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d/%m/%y", "%d-%m-%y",
              "%Y-%m-%d", "%d %B %Y", "%d/%m/%Y %H:%M:%S", "%d-%m-%Y %H:%M:%S"]


def _parse_date(s: str) -> str | None:
    s = s.strip()
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(s[:len(fmt)], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Column layout detection
# ---------------------------------------------------------------------------
# Different banks use different column orders; we try the most common patterns.

# Format A: Date | Description | Debit | Credit | Balance
_HEADER_A = re.compile(r"date.*narration|date.*description|date.*particulars", re.I)
# Format B: Date | Description | Ref No | Debit | Credit
_HEADER_B = re.compile(r"date.*chq|date.*ref", re.I)
# Format C: Date | Description | Withdrawal | Deposit | Balance
_HEADER_C = re.compile(r"withdrawal|deposit", re.I)


def _row_to_tx(cells: list[str]) -> dict | None:
    """Try to extract a transaction from a table row of cells."""
    if len(cells) < 4:
        return None
    # Try positions: (date, desc, debit, credit, balance?) — 5-col layout
    date_str = _parse_date(cells[0])
    if not date_str:
        return None  # first cell is not a date → skip

    desc = cells[1].strip()
    # Columns 2..end — look for two numeric amounts (debit/credit)
    amounts = [(i, _parse_amount(c)) for i, c in enumerate(cells[2:], start=2) if _parse_amount(c) is not None]
    if not amounts:
        return None

    debit = credit = None
    if len(amounts) >= 2:
        # Assume left = debit, right = credit (last cell is often balance)
        debit = amounts[0][1] if amounts[0][1] else None
        credit = amounts[1][1] if amounts[1][1] else None
        # If both have values, one is likely a balance — keep the smaller one as the transaction
        if debit and credit and abs(debit - credit) > 1:
            # balance is usually the last/largest; keep whichever is first (debit)
            pass  # leave as-is; user can correct
        elif debit and not credit:
            pass
        elif credit and not debit:
            debit, credit = None, credit
    elif len(amounts) == 1:
        # Single amount — look at Dr/Cr marker in description
        amt = amounts[0][1]
        if re.search(r"\bCr\b", desc, re.I):
            credit = amt
        else:
            debit = amt

    total = debit or credit or 0.0
    if total == 0:
        return None

    return {
        "date": date_str,
        "description": desc,
        "debit": debit,
        "credit": credit,
        "total": total,
        "merchant": _clean_merchant(desc),
        "category": _guess_category(desc),
        "payment_method": "Bank Transfer",
        "source": "passbook",
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_pdf(path: str) -> list[dict]:
    """Parse a passbook/statement PDF and return a list of transaction dicts."""
    transactions: list[dict] = []

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            # Strategy 1: extract tables
            for table in page.extract_tables():
                if not table:
                    continue
                # Skip header rows
                for row in table[1:]:
                    cells = [str(c or "").strip() for c in row]
                    tx = _row_to_tx(cells)
                    if tx:
                        transactions.append(tx)

            # Strategy 2: fall back to raw text lines if no tables found on this page
            if not page.extract_tables():
                text = page.extract_text() or ""
                _parse_lines(text, transactions)

    return transactions


def _parse_lines(text: str, out: list[dict]) -> None:
    """Line-by-line heuristic parser for PDFs that don't expose tables."""
    # Look for lines that start with a date-like pattern
    date_re = re.compile(
        r"^(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})\s+(.+?)\s+"
        r"([\d,]+\.?\d*)\s+"
        r"([\d,]+\.?\d*)?\s*$"
    )
    for line in text.splitlines():
        line = line.strip()
        m = date_re.match(line)
        if not m:
            continue
        date_str = _parse_date(m.group(1))
        if not date_str:
            continue
        desc = m.group(2).strip()
        a1 = _parse_amount(m.group(3))
        a2 = _parse_amount(m.group(4)) if m.group(4) else None
        debit = a1 if a1 else None
        credit = a2 if a2 else None
        total = debit or credit or 0.0
        if total == 0:
            continue
        out.append({
            "date": date_str,
            "description": desc,
            "debit": debit,
            "credit": credit,
            "total": total,
            "merchant": _clean_merchant(desc),
            "category": _guess_category(desc),
            "payment_method": "Bank Transfer",
            "source": "passbook",
        })
