"""Drill-down analysis over a user's confirmed receipts, filtered by any mix of category, merchant and month."""
import calendar
import math
import statistics
from collections import Counter, defaultdict
from datetime import date, timedelta

from insights import inr, month_name

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def prev_month(ym):
    y, m = map(int, ym.split("-"))
    return f"{y - (m == 1)}-{(m - 2) % 12 + 1:02d}"


def weekday_counts(first, last):
    """How many Mondays, Tuesdays, ... fall between two dates, to turn weekday totals into per-day averages."""
    counts, d = Counter(), first
    while d <= last:
        counts[d.weekday()] += 1
        d += timedelta(days=1)
    return counts


def monthly_merchants(bills):
    """Merchants that bill about once a month for a steady amount (rent, subscriptions, utilities)."""
    by_merchant = defaultdict(list)
    for b in bills:
        by_merchant[b.get("merchant")].append(b)
    found = set()
    for m, bs in by_merchant.items():
        months = sorted({b["date"][:7] for b in bs})
        if not m or len(months) < 3:
            continue
        (y0, m0), (y1, m1) = (map(int, months[0].split("-")), map(int, months[-1].split("-")))
        span = (y1 - y0) * 12 + m1 - m0 + 1
        med = statistics.median(b["total"] for b in bs)
        # regular (most months in its span), about once a month, steady amount; scattered big buys don't qualify
        if len(months) >= 0.75 * span and len(bs) <= 1.2 * len(months) and all(abs(b["total"] - med) <= 0.25 * med for b in bs):
            found.add(m)
    return found


def typical(bills, history=None):
    """Bills that describe habits: no monthly bills (rent always lands on the 5th), and no unusually large one-offs,
    judged on a log scale (above Q3 + 3*IQR of log amounts) because spending varies by multiples, not by rupees.
    Both rules are learned from `history` (default: the bills themselves), so a one-month view uses the full record."""
    history = bills if history is None else history
    monthly = monthly_merchants(history)
    positive = [b["total"] for b in history if b.get("merchant") not in monthly and b["total"] > 0]
    limit = math.inf
    if len(positive) >= 8:
        q1, _, q3 = statistics.quantiles([math.log(t) for t in positive], n=4)
        limit = math.exp(q3 + 3 * (q3 - q1))
    rest = [b for b in bills if b.get("merchant") not in monthly]
    keep = [b for b in rest if b["total"] <= limit]
    return keep, len(rest) - len(keep), monthly & {b.get("merchant") for b in bills}


def analysis(con, uid, category=None, merchant=None, month=None, today=None):
    today = today or date.today()
    base = "from receipts where user_id = ? and status = 'confirmed' and total is not null and date is not null"
    where, args = base, [uid]
    for col, val in (("category", category), ("merchant", merchant)):
        if val:
            where += f" and {col} = ?"
            args.append(val)
    if month:
        where += " and substr(date, 1, 7) = ?"
        args.append(month)
    q = lambda sql, extra=(): [dict(r) for r in con.execute(sql, (*args, *extra))]

    s = q(f"select count(*) as count, coalesce(sum(total), 0) as total, coalesce(avg(total), 0) as avg, "
          f"coalesce(sum(tax), 0) as tax, min(date) as first, max(date) as last {where}")[0]
    if not s["count"]:
        return {"summary": s}
    # share of the same period's spending, ignoring the category/merchant filter
    period = [uid] + ([month] if month else [])
    whole = con.execute(f"select coalesce(sum(total), 0) {base}" + (" and substr(date, 1, 7) = ?" if month else ""), period).fetchone()[0]
    s["share"] = s["total"] / whole * 100 if whole else None

    out = {"summary": s}
    if month:
        out["by_day"] = q(f"select date, sum(total) as total, count(*) as count {where} group by date order by date")
    else:
        out["by_month"] = q(f"select substr(date, 1, 7) as month, sum(total) as total, count(*) as count {where} group by 1 order by 1")
    if not category and not merchant:
        out["by_category"] = q(f"select coalesce(category, 'Other') as name, sum(total) as total, count(*) as count {where} group by 1 order by 2 desc")
    if not merchant:
        out["by_merchant"] = q(f"select merchant as name, sum(total) as total, count(*) as visits, avg(total) as avg "
                               f"{where} and merchant is not null group by merchant order by 2 desc limit 10")
    out["top_items"] = q(
        f"""select min(i.name) as name, count(distinct i.receipt_id) as bills, sum(i.qty) as qty, sum(i.price) as spent
            from items i where i.receipt_id in (select id {where}) group by lower(trim(i.name)) order by bills desc, spent desc limit 10""")
    out["by_payment"] = q(f"select coalesce(payment_method, 'Unknown') as name, sum(total) as total, count(*) as count {where} group by 1 order by 2 desc")

    # weekday pattern: average spend per calendar day of that weekday, so 5 Saturdays vs 4 don't skew it
    first, last = date.fromisoformat(s["first"]), date.fromisoformat(s["last"])
    if month:
        y, m = map(int, month.split("-"))
        first, last = date(y, m, 1), min(date(y, m, calendar.monthrange(y, m)[1]), today)
    days = weekday_counts(first, last)
    history = where if not month else where.replace(" and substr(date, 1, 7) = ?", "")
    hist_args = args if not month else args[:-1]
    bills, dropped, monthly = typical(q(f"select date, total, merchant {where}"),
                                      [dict(r) for r in con.execute(f"select date, total, merchant {history}", hist_args)])
    # patterns below describe habits, so they skip monthly bills and one-off huge bills
    out["outliers_ignored"], out["monthly_ignored"] = dropped, sorted(monthly)
    totals = defaultdict(float)
    for b in bills:
        totals[date.fromisoformat(b["date"]).weekday()] += b["total"]
    out["by_weekday"] = [{"day": WEEKDAYS[i], "avg": totals[i] / days[i] if days[i] else 0} for i in range(7)]
    by_month = defaultdict(float)
    for b in bills:
        by_month[b["date"][:7]] += b["total"]
    out["typical_by_month"] = dict(sorted(by_month.items()))

    if month and not merchant:  # what moved vs the previous month, by category (or by merchant inside a category)
        out["changes"] = changes(con, uid, month, category, today)
    out["receipts"] = q(f"select id, date, merchant, category, total, tax {where} order by date desc, id desc limit 100")
    out["insights"] = slice_insights(out, category, merchant, month, today)
    return out


def changes(con, uid, month, category, today):
    """Per-group spend this month vs last. While the month is running, compare the same days of both."""
    dim = "merchant" if category else "coalesce(category, 'Other')"
    cutoff = today.day if month == today.strftime("%Y-%m") else 31
    sql = f"""select {dim} as name, sum(total) as total from receipts
              where user_id = ? and status = 'confirmed' and total is not null and substr(date, 1, 7) = ?
              and cast(substr(date, 9, 2) as integer) <= ? {"and category = ?" if category else ""} group by 1"""
    extra = [category] if category else []
    now = {r["name"]: r["total"] for r in con.execute(sql, [uid, month, cutoff, *extra])}
    before = {r["name"]: r["total"] for r in con.execute(sql, [uid, prev_month(month), cutoff, *extra])}
    rows = [{"name": k, "now": now.get(k, 0), "before": before.get(k, 0), "delta": now.get(k, 0) - before.get(k, 0)}
            for k in set(now) | set(before)]
    rows.sort(key=lambda r: abs(r["delta"]), reverse=True)
    return {"prev_month": prev_month(month), "through_day": cutoff if cutoff < 31 else None, "rows": rows[:8]}


def slice_insights(a, category, merchant, month, today):
    s, out = a["summary"], []
    n = a["outliers_ignored"]
    skipped = (["monthly bills"] if a["monthly_ignored"] else []) + ([f"{n} unusually large bill{'s' if n != 1 else ''}"] if n else [])
    ignoring = f" (not counting {' or '.join(skipped)})" if skipped else ""
    label = merchant or category or (month_name(month) if month else "your spending")

    if s.get("share") is not None and (category or merchant) and s["share"] < 99.5:
        scope = f"of {month_name(month)}'s spending" if month else "of everything you've spent"
        out.append(("info", f"{label} is {s['share']:.0f}% {scope}: {inr(s['total'])} over {s['count']} bills, {inr(s['avg'])} each on average."))

    weeks = max((date.fromisoformat(s["last"]) - date.fromisoformat(s["first"])).days / 7, 1)
    if not month and s["count"] >= 8:
        out.append(("info", f"That's about {s['count'] / weeks:.1f} bills a week."))

    wd = a["by_weekday"]
    top = max(wd, key=lambda d: d["avg"])
    others = [d["avg"] for d in wd if d is not top and d["avg"]]
    # one month has only 4-5 of each weekday: too noisy to call a habit, so only across months
    if not month and others and top["avg"] > 1.3 * (sum(others) / len(others)):
        names = {"Mon": "Mondays", "Tue": "Tuesdays", "Wed": "Wednesdays", "Thu": "Thursdays", "Fri": "Fridays", "Sat": "Saturdays", "Sun": "Sundays"}
        out.append(("info", f"You spend the most on {names[top['day']]}: {inr(top['avg'])} on an average {top['day']}, "
                            f"{top['avg'] / (sum(others) / len(others)):.1f}× the other days{ignoring}."))

    full = [v for m, v in a["typical_by_month"].items() if m < today.strftime("%Y-%m")] if not month else []  # finished months
    if len(full) >= 6:
        recent, earlier = sum(full[-3:]), sum(full[-6:-3])
        if earlier and abs(recent / earlier - 1) >= 0.1:
            change = (recent / earlier - 1) * 100
            out.append(("warn" if change > 0 else "ok",
                        f"{'Up' if change > 0 else 'Down'} {abs(change):.0f}% over the last 3 full months compared with the 3 before{ignoring}."))

    if a["top_items"]:
        it = a["top_items"][0]
        out.append(("info", f"Most bought: {it['name']}, on {it['bills']} bills ({inr(it['spent'])} in total)."))

    ch = a.get("changes")
    if ch and ch["rows"]:
        r = ch["rows"][0]
        if abs(r["delta"]) >= 100:
            days = f" (days 1–{ch['through_day']})" if ch["through_day"] else ""
            out.append(("warn" if r["delta"] > 0 else "ok",
                        f"Biggest change vs {month_name(ch['prev_month'])}{days}: {r['name']} "
                        f"{'up' if r['delta'] > 0 else 'down'} {inr(abs(r['delta']))} ({inr(r['before'])} → {inr(r['now'])})."))
    return [{"level": lvl, "message": msg} for lvl, msg in out]


if __name__ == "__main__":
    import sqlite3

    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""create table receipts (id integer primary key, user_id int, status text, date text, merchant text,
                         category text, total real, tax real, payment_method text);
                         create table items (receipt_id int, name text, qty real, price real);""")
    rows = [(1, "2026-08-01", "Chai", "Food", 20), (1, "2026-08-02", "Chai", "Food", 20), (1, "2026-08-08", "Chai", "Food", 20),
            (1, "2026-08-20", "Mall", "Shopping", 1000), (1, "2026-09-05", "Chai", "Food", 20), (1, "2026-09-06", "Mall", "Shopping", 3000),
            (2, "2026-09-06", "Secret", "Food", 99999)]  # another user's data must never leak in
    for i, (u, d, m, c, t) in enumerate(rows):
        con.execute("insert into receipts values (?, ?, 'confirmed', ?, ?, ?, ?, 0, 'UPI')", (i + 1, u, d, m, c, t))
        con.execute("insert into items values (?, ?, 1, ?)", (i + 1, "Cutting chai" if m == "Chai" else "Jeans", t))
    today = date(2026, 9, 10)

    a = analysis(con, 1, today=today)
    assert a["summary"]["count"] == 6 and a["summary"]["total"] == 4080, a["summary"]
    assert [c["name"] for c in a["by_category"]] == ["Shopping", "Food"]
    assert a["top_items"][0]["name"] == "Cutting chai" and a["top_items"][0]["bills"] == 4

    f = analysis(con, 1, category="Food", today=today)
    assert f["summary"]["total"] == 80 and round(f["summary"]["share"], 1) == 2.0 and "by_category" not in f
    sat = next(d for d in f["by_weekday"] if d["day"] == "Sat")  # Aug 1, Aug 8, Sep 5 are Saturdays: 60 over 6 Saturdays
    assert sat["avg"] == 10, f["by_weekday"]

    sep = analysis(con, 1, month="2026-09", today=today)
    assert [d["date"] for d in sep["by_day"]] == ["2026-09-05", "2026-09-06"] and "by_month" not in sep
    top = sep["changes"]["rows"][0]  # Aug 1-10 had no Shopping (Mall was the 20th), so +3000
    assert (top["name"], top["before"], top["now"]) == ("Shopping", 0, 3000) and sep["changes"]["through_day"] == 10, sep["changes"]
    assert analysis(con, 1, merchant="Nobody", today=today) == {"summary": {"count": 0, "total": 0, "avg": 0, "tax": 0, "first": None, "last": None}}
    spiky = [{"date": f"2026-08-{d:02d}", "total": t, "merchant": "Cafe"} for d, t in enumerate([15, 20, 25, 30, 35, 40, 45, 50, 60, 80, 100, 120, 140, 450, 380, 64888], 1)]
    kept, dropped, _ = typical(spiky)
    assert dropped == 1 and max(b["total"] for b in kept) == 450, (kept, dropped)  # skewed but normal bills stay
    rent = [{"date": f"2026-{m:02d}-05", "total": 7500, "merchant": "PG"} for m in (6, 7, 8)]
    assert monthly_merchants(rent + spiky) == {"PG"} and typical(spiky[:5] + rent)[0] == spiky[:5]
    scattered = [{"date": f"{d}-10", "total": 60000, "merchant": "Croma"} for d in ("2025-04", "2025-11", "2026-06")]
    assert monthly_merchants(scattered) == set()  # 3 buys across 15 months is not a monthly bill
    month_view, _, skipped = typical(rent[-1:] + spiky[:3], history=rent + spiky)  # one month, rules from history
    assert skipped == {"PG"} and month_view == spiky[:3]
    print("analysis ok")
