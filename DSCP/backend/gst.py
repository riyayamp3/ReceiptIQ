"""GST knowledge: expected rate per service type and date, and checks against what a receipt charged.

Rates are standard Indian GST rates (combined CGST+SGST, or IGST), in percent. Two periods matter:
GST began 2017-07-01, and the Sept 2025 reform (effective 2025-09-22) moved most goods and services to
5% / 18% slabs. Rates marked as a set depend on price or class (e.g. hotel room tariff, clothing price).
ponytail: hand-maintained table, verify against CBIC notifications and update when the Council changes rates.
"""
import re
from datetime import date

GST_START = "2017-07-01"
REFORM_2025 = "2025-09-22"

# service type: (label, rates before reform, rates from reform, note). Empty set = outside GST.
SERVICES = {
    "restaurant": ("Restaurant / café / food delivery", {5}, {5}, "Restaurants charge 5% (18% only inside hotels with rooms above ₹7,500)."),
    "groceries": ("Groceries / packaged food", {0, 5, 12, 18}, {0, 5, 18}, "Fresh produce is exempt; packaged food is mostly 5%."),
    "clothing": ("Clothing / footwear", {5, 12}, {5, 18}, "Before Sept 2025: 5% up to ₹1,000 a piece, else 12%. Now 5% up to ₹2,500, else 18%."),
    "hotel_stay": ("Hotel stay", {0, 12, 18}, {5, 18}, "Rooms up to ₹7,500 a night: 12% before Sept 2025, 5% now; above that 18%."),
    "cab": ("Cab / auto / ride-hailing", {5}, {5}, "App cabs and rentals charge 5%."),
    "train": ("Train travel / railway catering", {0, 5}, {0, 5}, "Non-AC tickets are exempt; AC tickets and IRCTC meals are 5%."),
    "air": ("Air travel", {5, 12}, {5, 18}, "Economy 5%; business class 12% before Sept 2025, 18% now."),
    "fuel": ("Petrol / diesel", set(), set(), "Petrol and diesel are outside GST; the price includes state VAT and excise instead."),
    "electricity": ("Electricity", {0}, {0}, "Electricity is exempt from GST."),
    "telecom": ("Mobile / internet / DTH", {18}, {18}, "Telecom and internet services are 18%."),
    "subscription": ("Streaming / software subscription", {18}, {18}, "Online services like OTT and software are 18%."),
    "medicine": ("Medicines", {5, 12}, {0, 5}, "Most medicines 12% or 5% before Sept 2025; now 5%, life-saving drugs exempt."),
    "healthcare": ("Doctor / hospital / diagnostics", {0}, {0}, "Healthcare services are exempt."),
    "insurance": ("Insurance premium", {18}, {0, 18}, "Individual health and life insurance became exempt in Sept 2025."),
    "salon_gym": ("Salon / gym / spa", {18}, {5}, "18% before Sept 2025, 5% now."),
    "movies": ("Movies / events", {12, 18}, {5, 18}, "Tickets up to ₹100: 12% then, 5% now; costlier tickets 18%."),
    "electronics": ("Electronics / appliances", {18, 28}, {18}, "ACs, TVs and similar dropped from 28% to 18% in Sept 2025."),
    "professional": ("Professional services (legal, CA, repairs)", {18}, {18}, "Professional and repair services are 18%."),
    "jewellery": ("Jewellery", {3}, {3}, "Gold and jewellery are 3%."),
    "other": ("Other", None, None, ""),
}
GSTIN = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


def standard_rate(service, day, price=0):
    """The one rate a correctly-billing registered business charges; None if outside GST or unknown.
    price is per unit (per garment, ticket, room-night), which decides the slab for some services."""
    after = day >= REFORM_2025
    by_price = {
        "clothing": (5 if price <= 2500 else 18) if after else (5 if price <= 1000 else 12),
        "hotel_stay": (5 if price <= 7500 else 18) if after else (12 if price <= 7500 else 18),
        "movies": (5 if price <= 100 else 18) if after else (12 if price <= 100 else 18),
        "medicine": 5 if after else 12, "insurance": 0 if after else 18, "salon_gym": 5 if after else 18,
        "air": 5, "train": 5, "groceries": 5, "electronics": 18,  # economy flights, AC trains, packaged food
    }
    if service in by_price:
        return by_price[service]
    allowed = SERVICES.get(service, SERVICES["other"])[2 if after else 1]
    return max(allowed) if allowed else None  # single-rate services; empty set = outside GST


def effective_rate(r):
    """Combined GST % as printed, else tax / taxable value."""
    if r.get("gst_rate"):
        return float(r["gst_rate"])
    tax, total, subtotal = r.get("tax"), r.get("total"), r.get("subtotal")
    base = subtotal or (total - tax if total and tax else None)
    return round(tax / base * 100, 1) if tax and base else None


def check(r):
    """List of (level, message) about the receipt's GST; level is ok / info / warn."""
    d, tax = r.get("date"), r.get("tax") or 0
    if d and d < GST_START:
        return [("info", "Bill predates GST (July 2017), so any tax on it is VAT or service tax.")]
    label, before, after, note = SERVICES.get(r.get("service_type") or "other", SERVICES["other"])
    notes = []
    allowed = (after if d and d >= REFORM_2025 else before) if d else (before or set()) | (after or set())
    rate = effective_rate(r)
    if allowed is None:
        notes.append(("info", "Pick a service type to check the GST rate."))
    elif not allowed:
        notes.append(("warn", f"{label} is outside GST, but this bill charges tax.") if tax else ("ok", note))
    elif not tax:
        notes.append(("ok" if 0 in allowed else "info", note if 0 in allowed else
                      f"No GST charged. {label} is normally {'/'.join(f'{x}%' for x in sorted(allowed))}; "
                      "small businesses under the composition scheme don't add GST."))
    elif rate is not None:
        expected = "/".join(f"{x}%" for x in sorted(allowed))
        if any(abs(rate - x) <= 0.6 for x in allowed):
            notes.append(("ok", f"GST {rate:g}% matches the rate for {label}. {note}"))
        else:
            notes.append(("warn", f"GST works out to {rate:g}%, but {label} is normally {expected}. {note}"))
    cgst, sgst, igst = r.get("cgst"), r.get("sgst"), r.get("igst")
    if cgst and sgst and abs(cgst - sgst) > 0.05:
        notes.append(("warn", f"CGST (₹{cgst:g}) and SGST (₹{sgst:g}) should be equal."))
    if igst and (cgst or sgst):
        notes.append(("warn", "A bill charges either IGST (inter-state) or CGST+SGST (same state), not both."))
    gstin = (r.get("gstin") or "").replace(" ", "").upper()
    if tax and not gstin:
        notes.append(("info", "GST charged but no GSTIN found; a valid GST bill must show the seller's GSTIN."))
    elif gstin and not GSTIN.match(gstin):
        notes.append(("warn", f"GSTIN {gstin} doesn't look valid (15 characters: state code, PAN, entity, Z, check)."))
    return notes


if __name__ == "__main__":
    levels = lambda r: [lvl for lvl, _ in check(r)]
    # hw_2: legal firm, CGST 9% + SGST 9% on service charges
    assert levels({"date": "2024-11-15", "service_type": "professional", "tax": 1250, "gst_rate": 18, "cgst": 625, "sgst": 625, "gstin": "09GBVPS8212J1ZP"}) == ["ok"]
    # IRCTC meal: 2.5% + 2.5%, derived from amounts (3.80 on 76.19)
    assert levels({"date": "2025-01-22", "service_type": "train", "tax": 3.8, "subtotal": 76.19, "total": 80, "gstin": "19AAGFB1374P2ZY"}) == ["ok"]
    assert levels({"date": "2025-03-19", "service_type": "fuel", "total": 1000}) == ["ok"]  # petrol: outside GST
    assert levels({"date": "2007-05-22", "service_type": "restaurant", "total": 146}) == ["info"]  # pre-GST
    assert levels({"date": "2020-12-17", "service_type": "restaurant", "tax": 0, "total": 415}) == ["info"]  # composition scheme
    # restaurant charging 18%, unequal halves, no GSTIN
    assert levels({"date": "2024-01-01", "service_type": "restaurant", "tax": 180, "subtotal": 1000, "cgst": 100, "sgst": 80}) == ["warn", "warn", "info"]
    # salon: 18% was right before the reform, wrong after it
    salon = {"service_type": "salon_gym", "tax": 180, "subtotal": 1000, "gstin": "27AAPFU0939F1ZV"}
    assert levels({**salon, "date": "2025-09-01"}) == ["ok"] and levels({**salon, "date": "2025-10-01"}) == ["warn"]
    # standard_rate always lands inside the allowed set for its period
    for svc, (_, before, after, _) in SERVICES.items():
        for day in ("2024-06-01", "2025-10-01"):
            for price in (80, 900, 2000, 5000, 9000):
                rate = standard_rate(svc, day, price)
                allowed = after if day >= REFORM_2025 else before
                assert (rate is None) == (not allowed), (svc, day, rate)
                assert rate is None or rate in allowed, (svc, day, price, rate)
    print("gst ok")
