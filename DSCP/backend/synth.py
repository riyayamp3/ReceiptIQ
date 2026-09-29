"""Synthetic spending data: realistic Indian personas with ground-truth anomaly labels.

Real receipts are too few for forecasting, anomaly detection or clustering, so we simulate 18 months of
spending for three personas. The generator knows the truth (which transactions are anomalies, which are
recurring), which is what lets us *measure* the models later.

  python backend/synth.py --csv data/          write transactions.csv + items.csv (with labels)
  python backend/synth.py --load               (re)create demo accounts in the app database (no labels)
  python backend/synth.py --check              self-check
"""
import argparse
import csv
import json
import math
import random
import string
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import gst

START = date(2025, 4, 1)  # spans the Sept 2025 GST reform on purpose
FESTIVE_MONTHS = {10, 11}  # Diwali season
ANOMALY_RATE = 0.015  # per anomaly type, as a share of normal transactions


@dataclass
class Merchant:
    name: str
    category: str
    service: str
    per_week: float  # average visits per week
    items: list  # (name, price) or (name, low, high); prices are before GST
    basket: tuple = (1,)  # how many different items one bill can have
    weekend: float = 1.0  # visit multiplier on Sat/Sun
    festive: float = 1.0  # visit multiplier in Oct/Nov
    small: bool = False  # composition-scheme / unregistered shop: no GST, no GSTIN


@dataclass
class Recurring:
    name: str
    category: str
    service: str
    item: str
    price: tuple  # (price,) fixed or (low, high) variable, before GST
    day: int = 1  # day of month, or start offset for every_days
    every_days: int = 0  # 0 = monthly
    months: tuple = ()  # only these months (quarterly bills)
    small: bool = False


@dataclass
class Persona:
    key: str
    name: str
    email: str
    state: int  # GST state code (first two digits of a GSTIN)
    budget: float
    payments: dict
    merchants: list
    recurring: list
    unusual: list = field(default_factory=list)  # out-of-profile big-ticket merchants, used for anomalies


M, R = Merchant, Recurring
PERSONAS = [
    Persona(
        "student", "Aarav Kulkarni", "student@demo.receiptiq.in", 27, 20000,
        {"UPI": 0.8, "Cash": 0.15, "Card": 0.05},
        [
            M("Amruttulya Chai", "Food & Dining", "restaurant", 6, [("Cutting chai", 15), ("Bun maska", 30), ("Poha", 35)], (1, 2), small=True),
            M("College Canteen", "Food & Dining", "restaurant", 4, [("Veg thali", 70), ("Samosa", 20), ("Cold coffee", 50), ("Vada pav", 20)], (1, 2), weekend=0.2, small=True),
            M("Swiggy", "Food & Dining", "restaurant", 1.5, [("Chicken biryani", 280), ("Paneer butter masala", 240), ("Butter naan", 45), ("Veg fried rice", 190), ("Gulab jamun", 80)], (1, 2, 3), weekend=1.8),
            M("Domino's", "Food & Dining", "restaurant", 0.4, [("Farmhouse pizza", 459), ("Garlic breadsticks", 129), ("Pepsi 500ml", 60)], (1, 2, 3), weekend=2),
            M("D-Mart", "Groceries", "groceries", 0.5, [("Maggi noodles 12-pack", 168), ("Amul milk 1L", 68), ("Bread", 45), ("Bournvita 500g", 245), ("Detergent 1kg", 199), ("Biscuits", 40)], (2, 3, 4), weekend=2),
            M("Rapido", "Travel", "cab", 2, [("Bike taxi ride", 45, 140)]),
            M("Uber", "Travel", "cab", 0.4, [("Uber Go ride", 150, 420)], weekend=2),
            M("PVR Cinemas", "Entertainment", "movies", 0.15, [("Movie ticket", 180, 350), ("Popcorn combo", 320)], (1, 2), weekend=3),
            M("Myntra", "Shopping", "clothing", 0.12, [("T-shirt", 599), ("Jeans", 1799), ("Sneakers", 2499), ("Hoodie", 1299)], (1, 2), festive=3),
            M("Stationery Mart", "Shopping", "other", 0.3, [("Notebook", 60), ("Pens pack", 50), ("Printouts", 20, 120)], (1, 2), weekend=0.3, small=True),
        ],
        [
            R("Sunrise PG", "Bills & Utilities", "other", "PG rent", (7500,), day=5, small=True),
            R("Jio", "Bills & Utilities", "telecom", "Prepaid recharge 28 days", (253,), day=3, every_days=28),
            R("Spotify", "Entertainment", "subscription", "Spotify Premium", (101,), day=18),
        ],
        [M("Croma", "Shopping", "electronics", 0, [("iPhone 15", 62000), ("Laptop", 54990)]),
         M("MakeMyTrip", "Travel", "air", 0, [("Flight Pune-Delhi", 8400)]),
         M("Tanishq", "Shopping", "jewellery", 0, [("Gold chain", 45000)])],
    ),
    Persona(
        "professional", "Priya Menon", "professional@demo.receiptiq.in", 29, 80000,
        {"UPI": 0.5, "Card": 0.45, "Cash": 0.05},
        [
            M("Starbucks", "Food & Dining", "restaurant", 3, [("Caffe latte", 320), ("Cold brew", 290), ("Blueberry muffin", 210), ("Croissant", 250)], (1, 2), weekend=0.5),
            M("Swiggy", "Food & Dining", "restaurant", 2.5, [("Chicken biryani", 320), ("Ramen", 450), ("Caesar salad", 380), ("Pad thai", 420), ("Cheesecake", 260)], (1, 2, 3), weekend=1.5),
            M("Truffles", "Food & Dining", "restaurant", 0.5, [("Burger", 350), ("Fries", 180), ("Milkshake", 220), ("Pasta", 390)], (2, 3, 4), weekend=2.5),
            M("BigBasket", "Groceries", "groceries", 1, [("Vegetables box", 450), ("Greek yogurt", 120), ("Avocado x2", 260), ("Almond milk 1L", 299), ("Eggs 12", 108), ("Olive oil 1L", 899)], (2, 3, 4, 5), weekend=2),
            M("Uber", "Travel", "cab", 4, [("Uber Go ride", 180, 520)]),
            M("Amazon", "Shopping", "electronics", 0.15, [("Bluetooth earbuds", 2999), ("Phone charger", 1299), ("Smartwatch", 4999)], festive=3),
            M("Zara", "Shopping", "clothing", 0.2, [("Linen shirt", 2990), ("Trousers", 3290), ("Dress", 3990), ("T-shirt", 1290)], (1, 2), weekend=2.5, festive=2.5),
            M("Toni&Guy", "Health", "salon_gym", 0.1, [("Haircut", 900), ("Hair spa", 1800)]),
            M("Apollo Pharmacy", "Health", "medicine", 0.2, [("Paracetamol strip", 35), ("Vitamin D3", 310), ("Cough syrup", 120)], (1, 2)),
            M("PVR Cinemas", "Entertainment", "movies", 0.3, [("Movie ticket", 250, 450), ("Nachos combo", 380)], (1, 2), weekend=3),
            M("IndiGo", "Travel", "air", 0.03, [("Flight BLR-DEL", 6500, 11000)]),
            M("Lemon Tree Hotels", "Travel", "hotel_stay", 0.02, [("Room night", 4500, 7000)]),
        ],
        [
            R("Rent - Koramangala flat", "Bills & Utilities", "other", "House rent", (22000,), day=2, small=True),
            R("Netflix", "Entertainment", "subscription", "Netflix Premium", (550,), day=12),
            R("Cult.fit", "Health", "salon_gym", "Gym membership", (1695,), day=1),
            R("Airtel Xstream Fiber", "Bills & Utilities", "telecom", "Broadband 100 Mbps", (847,), day=7),
            R("BESCOM", "Bills & Utilities", "electricity", "Electricity bill", (1100, 2200), day=15),
        ],
        [M("Apple Store", "Shopping", "electronics", 0, [("MacBook Air", 99900)]),
         M("Emirates", "Travel", "air", 0, [("Flight BLR-Dubai", 42000)]),
         M("Louis Vuitton", "Shopping", "clothing", 0, [("Handbag", 145000)])],
    ),
    Persona(
        "family", "Sharma Family", "family@demo.receiptiq.in", 27, 65000,
        {"UPI": 0.6, "Cash": 0.25, "Card": 0.15},
        [
            M("Gupta Kirana Store", "Groceries", "groceries", 3, [("Atta 10kg", 480), ("Toor dal 1kg", 165), ("Rice 5kg", 390), ("Sugar 1kg", 46), ("Tea 500g", 260), ("Mustard oil 1L", 185), ("Vegetables", 120, 350)], (2, 3, 4, 5), small=True),
            M("D-Mart", "Groceries", "groceries", 1, [("Detergent 2kg", 380), ("Toothpaste", 110), ("Biscuits family pack", 120), ("Ghee 1L", 620), ("Soap 4-pack", 180), ("Namkeen", 90)], (3, 4, 5, 6), weekend=2.5),
            M("Haldiram's", "Food & Dining", "restaurant", 0.6, [("Chole bhature", 180), ("Raj kachori", 160), ("Masala dosa", 170), ("Rasmalai", 140), ("Thali", 320)], (2, 3, 4), weekend=2.5),
            M("Zomato", "Food & Dining", "restaurant", 0.8, [("Veg biryani", 260), ("Paneer tikka", 310), ("Dal makhani", 280), ("Tandoori roti", 35)], (2, 3, 4), weekend=1.5),
            M("HP Petrol Pump", "Travel", "fuel", 1, [("Petrol", 1000, 2500)]),
            M("MedPlus", "Health", "medicine", 0.5, [("BP tablets strip", 180), ("Diabetes medicine", 420), ("Vitamin tablets", 280), ("Bandages", 60)], (1, 2)),
            M("Dr. Joshi Clinic", "Health", "healthcare", 0.08, [("Consultation", 500, 800)]),
            M("Reliance Trends", "Shopping", "clothing", 0.2, [("Kurta", 899), ("Saree", 2499), ("Kids t-shirt", 399), ("School shoes", 1199)], (1, 2, 3), weekend=2.5, festive=4),
            M("Reliance Digital", "Shopping", "electronics", 0.03, [("Mixer grinder", 3499), ("LED TV 43in", 27990), ("Iron", 1299)], weekend=2, festive=2),
            M("Tanishq", "Shopping", "jewellery", 0.005, [("Gold earrings", 38000, 65000)], weekend=2, festive=20),
        ],
        [
            R("MSEDCL", "Bills & Utilities", "electricity", "Electricity bill", (1800, 4200), day=20),
            R("LIC", "Bills & Utilities", "insurance", "Life insurance premium", (12500,), day=10, months=(1, 4, 7, 10)),
            R("Tata Play", "Entertainment", "telecom", "DTH recharge", (297,), day=8),
            R("St. Xavier's School", "Bills & Utilities", "other", "School fees", (18000,), day=5, months=(4, 7, 10, 1), small=True),
            R("Jio Postpaid", "Bills & Utilities", "telecom", "Postpaid plan", (508,), day=25),
        ],
        [M("Apple Store", "Shopping", "electronics", 0, [("iPhone 16", 79900)]),
         M("Taj Exotica Goa", "Travel", "hotel_stay", 0, [("Room night", 9500, 12000)]),
         M("Maruti Service Centre", "Travel", "professional", 0, [("Car repair", 18000, 26000)])],
    ),
]


def poisson(rng, lam):
    """Knuth's method; fine for the small rates here."""
    k, p, limit = 0, 1.0, math.exp(-lam)
    while (p := p * rng.random()) > limit:
        k += 1
    return k


def gstin(rng, state):
    """Format-valid but synthetic GSTIN: state code, PAN-like block, entity, Z, check char."""
    letters = lambda n: "".join(rng.choices(string.ascii_uppercase, k=n))
    return f"{state:02d}{letters(5)}{rng.randrange(10000):04d}{letters(1)}1Z{rng.choice(string.ascii_uppercase + string.digits)}"


def bill(rng, persona, name, category, service, day, items, small, gstins, payment=None):
    """Build one receipt with GST computed the way a correctly-billing shop would."""
    subtotal = round(sum(i["price"] for i in items), 2)
    # ponytail: one rate per bill, from the priciest unit; real invoices rate each line separately
    rate = None if small else gst.standard_rate(service, day.isoformat(), max(i["price"] / i["qty"] for i in items))
    tax = round(subtotal * (rate or 0) / 100, 2)
    return {
        "date": day.isoformat(), "merchant": name, "category": category, "service_type": service,
        "payment_method": payment or rng.choices(list(persona.payments), list(persona.payments.values()))[0],
        "items": items, "subtotal": subtotal, "tax": tax,
        "cgst": round(tax / 2, 2) if tax else None, "sgst": round(tax / 2, 2) if tax else None, "igst": None,
        "gst_rate": rate or None, "gstin": None if small else gstins.setdefault(name, gstin(rng, persona.state)),
        "total": float(round(subtotal + tax)),  # bills round off to the rupee
        "recurring": False, "is_anomaly": False, "anomaly_type": "",
    }


def basket(rng, m):
    chosen = rng.sample(m.items, min(rng.choice(m.basket), len(m.items)))
    items = []
    for it in chosen:
        unit = it[1] if len(it) == 2 else round(rng.uniform(it[1], it[2]))
        qty = 2 if m.category == "Food & Dining" and rng.random() < 0.2 else 1
        items.append({"name": it[0], "qty": qty, "price": float(unit * qty)})
    return items


def generate(persona, start=START, end=None, seed=7):
    """All transactions for one persona, sorted by date, with ground-truth labels."""
    rng, gstins, end = random.Random(f"{persona.key}-{seed}"), {}, end or date.today()
    txs, day = [], start
    while day <= end:
        weekend, festive, payday = day.weekday() >= 5, day.month in FESTIVE_MONTHS, day.day <= 5
        for m in persona.merchants:
            lam = m.per_week / 7 * (m.weekend if weekend else 1) * (m.festive if festive else 1)
            lam *= 1.25 if payday and m.category in ("Shopping", "Food & Dining") else 1
            for _ in range(poisson(rng, lam)):
                txs.append(bill(rng, persona, m.name, m.category, m.service, day, basket(rng, m), m.small, gstins))
        for r in persona.recurring:
            due = ((day - start).days - r.day) % r.every_days == 0 if r.every_days else day.day == r.day
            if due and (not r.months or day.month in r.months):
                price = r.price[0] if len(r.price) == 1 else round(rng.uniform(*r.price))
                t = bill(rng, persona, r.name, r.category, r.service, day, [{"name": r.item, "qty": 1, "price": float(price)}],
                         r.small, gstins, payment="Card" if persona.payments.get("Card", 0) > 0.3 else "UPI")
                t["recurring"] = True
                txs.append(t)
        day += timedelta(days=1)

    # plant anomalies, each labelled with its type
    normal = [t for t in txs if not t["recurring"]]
    n = max(1, round(len(normal) * ANOMALY_RATE))
    for t in rng.sample(normal, n):  # price spike: same shop, several times the usual bill
        t["items"] = [{**i, "price": round(i["price"] * rng.uniform(4, 8))} for i in t["items"]]
        spiked = bill(rng, persona, t["merchant"], t["category"], t["service_type"], date.fromisoformat(t["date"]),
                      t["items"], t["gstin"] is None, gstins, t["payment_method"])
        t.update({**spiked, "is_anomaly": True, "anomaly_type": "spike"})
    for t in rng.sample([t for t in normal if not t["is_anomaly"]], n):  # duplicate charge the same day
        txs.append({**t, "items": [dict(i) for i in t["items"]], "is_anomaly": True, "anomaly_type": "duplicate"})
    days = (end - start).days
    for _ in range(n):  # a big purchase that doesn't fit the person
        m = rng.choice(persona.unusual)
        t = bill(rng, persona, m.name, m.category, m.service, start + timedelta(days=rng.randrange(days + 1)), basket(rng, m), False, gstins)
        t.update({"is_anomaly": True, "anomaly_type": "unusual_merchant"})
        txs.append(t)

    txs.sort(key=lambda t: t["date"])
    for i, t in enumerate(txs):
        t["tx_id"] = f"{persona.key}-{i:05d}"
    return txs


def write_csv(out_dir, end=None):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cols = ["tx_id", "persona", "date", "weekday", "merchant", "category", "service_type", "payment_method", "n_items",
            "subtotal", "tax", "cgst", "sgst", "gst_rate", "gstin", "total", "recurring", "is_anomaly", "anomaly_type"]
    with open(out / "transactions.csv", "w", newline="", encoding="utf-8") as ft, open(out / "items.csv", "w", newline="", encoding="utf-8") as fi:
        tw, iw = csv.DictWriter(ft, cols), csv.writer(fi)
        tw.writeheader()
        iw.writerow(["tx_id", "persona", "item", "qty", "price", "category"])
        total = 0
        for p in PERSONAS:
            for t in generate(p, end=end):
                tw.writerow({**{c: t.get(c) for c in cols}, "persona": p.key, "n_items": len(t["items"]),
                             "weekday": date.fromisoformat(t["date"]).strftime("%a")})
                for i in t["items"]:
                    iw.writerow([t["tx_id"], p.key, i["name"], i["qty"], i["price"], t["category"]])
                total += 1
    print(f"wrote {total} transactions to {out / 'transactions.csv'} and their items to {out / 'items.csv'}")


DEMO_PASSWORD = "demo12345"


def load_demo_accounts(end=None):
    """Replace the demo accounts in the app database with fresh synthetic data (labels are not stored)."""
    from auth import hash_password
    from db import connect, migrate

    migrate()
    with closing(connect()) as con, con:
        for p in PERSONAS:
            old = con.execute("select id from users where email = ?", (p.email,)).fetchone()
            if old:  # receipts.user_id may lack ON DELETE CASCADE in older databases, so delete explicitly
                con.execute("delete from receipts where user_id = ?", (old["id"],))
                con.execute("delete from users where id = ?", (old["id"],))
            uid = con.execute("insert into users (name, email, password_hash, monthly_budget) values (?, ?, ?, ?)",
                              (p.name, p.email, hash_password(DEMO_PASSWORD), p.budget)).lastrowid
            txs = generate(p, end=end)
            for t in txs:
                rid = con.execute(
                    """insert into receipts (user_id, merchant, date, subtotal, tax, total, payment_method, category,
                                             engine, lang, cgst, sgst, igst, gst_rate, gstin, service_type, status,
                                             raw_text, field_confidence)
                       values (?, ?, ?, ?, ?, ?, ?, ?, 'synthetic', 'en', ?, ?, ?, ?, ?, ?, 'confirmed', ?, '{}')""",
                    (uid, t["merchant"], t["date"], t["subtotal"], t["tax"], t["total"], t["payment_method"], t["category"],
                     t["cgst"], t["sgst"], t["igst"], t["gst_rate"], t["gstin"], t["service_type"],
                     f"Synthetic transaction {t['tx_id']}"),
                ).lastrowid
                con.executemany("insert into items (receipt_id, name, qty, price) values (?, ?, ?, ?)",
                                [(rid, i["name"], i["qty"], i["price"]) for i in t["items"]])
            print(f"{p.name:<16} {p.email:<32} {len(txs)} receipts")
    print(f"password for all demo accounts: {DEMO_PASSWORD}")


def check():
    end = date(2026, 9, 27)
    for p in PERSONAS:
        txs = generate(p, end=end)
        assert txs == generate(p, end=end), "same seed must give the same data"
        normal = [t for t in txs if not t["is_anomaly"]]
        share = sum(t["is_anomaly"] for t in txs) / len(txs)
        assert 0.03 < share < 0.06, (p.key, share)  # three anomaly types at ~1.5% each
        for t in txs:
            assert abs(sum(i["price"] for i in t["items"]) - t["subtotal"]) < 0.01, t
            assert abs(t["subtotal"] + t["tax"] - t["total"]) <= 0.5, t
        for t in normal:  # every normal bill must pass our own GST rules
            warns = [msg for lvl, msg in gst.check(t) if lvl == "warn"]
            assert not warns, (t, warns)
        months = {t["date"][:7] for t in txs if t["recurring"] and t["service_type"] == "subscription"}
        if p.key != "family":
            assert len(months) == 18, (p.key, len(months))  # a subscription in every month Apr 2025 - Sep 2026
        print(f"{p.key:<13} {len(txs):>5} transactions, {share:.1%} anomalies, "
              f"₹{sum(t['total'] for t in normal) / 18:,.0f}/month typical spend (budget ₹{p.budget:,.0f})")
    print("synth ok")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", metavar="DIR", help="write transactions.csv and items.csv with ground-truth labels")
    ap.add_argument("--load", action="store_true", help="(re)create the demo accounts in the app database")
    ap.add_argument("--check", action="store_true", help="run the self-check")
    ap.add_argument("--end", type=date.fromisoformat, help="last day to simulate (default: today)")
    args = ap.parse_args()
    if args.check or not (args.csv or args.load):
        check()
    if args.csv:
        write_csv(args.csv, args.end)
    if args.load:
        load_demo_accounts(args.end)
