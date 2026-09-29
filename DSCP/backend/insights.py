"""Personal insights computed from the user's confirmed receipts (plain statistics, no LLM, so nothing is invented)."""
import calendar
from collections import defaultdict
from datetime import date

import gst


def inr(x):
    return f"₹{x:,.0f}"


def month_name(ym):
    y, m = map(int, ym.split("-"))
    return f"{calendar.month_abbr[m]} {y}"


def budget_status(receipts, budget, today):
    """This calendar month's spend against the budget, with a straight-line month-end projection."""
    month = today.strftime("%Y-%m")
    spent = sum(r["total"] for r in receipts if (r["date"] or "").startswith(month))
    days = calendar.monthrange(today.year, today.month)[1]
    return {
        "month": month, "spent": spent, "budget": budget,
        "projected": spent / today.day * days, "days_left": days - today.day,
    }


def compare_months(receipts, today):
    """Latest month with spending vs the calendar month before it. While the latest month is still running,
    compare like with like: days 1..today of both months, not a partial month against a full one."""
    by_month = defaultdict(float)
    for r in receipts:
        by_month[r["date"][:7]] += r["total"]
    latest = max(by_month)
    y, m = map(int, latest.split("-"))
    prev = f"{y - (m == 1)}-{(m - 2) % 12 + 1:02d}"
    partial = latest == today.strftime("%Y-%m")
    prev_spent = by_month.get(prev)
    if partial and prev_spent is not None:
        prev_spent = sum(r["total"] for r in receipts if r["date"][:7] == prev and int(r["date"][8:10]) <= today.day)
    return {
        "month": latest, "spent": by_month[latest], "prev_month": prev, "prev_spent": prev_spent,
        "partial": partial, "through_day": today.day if partial else None,
        # no comparison when the previous month has no data at all: a gap makes the % meaningless
        "change": (by_month[latest] - prev_spent) / prev_spent * 100 if prev_spent else None,
    }


def insights(receipts, budget=None, today=None):
    """Short sentences about the user's own spending, most useful first. Needs receipts with a date and total."""
    today = today or date.today()
    rs = [r for r in receipts if r["date"] and r["total"] is not None]
    if not rs:
        return []
    out = []

    b = budget_status(rs, budget, today)
    if budget and b["spent"]:
        if b["spent"] > budget:
            out.append(("warn", f"You're {inr(b['spent'] - budget)} over your {inr(budget)} budget this month."))
        elif b["projected"] > budget:
            out.append(("warn", f"At this pace you'll spend about {inr(b['projected'])} this month, {inr(b['projected'] - budget)} over budget."))
        else:
            out.append(("ok", f"You're on track: {inr(b['spent'])} of {inr(budget)} spent with {b['days_left']} days to go."))

    by_month = defaultdict(float)
    for r in rs:
        by_month[r["date"][:7]] += r["total"]
    months = sorted(by_month)
    latest = months[-1]
    c = compare_months(rs, today)
    if c["change"] is not None and abs(c["change"]) >= 5:
        more = c["change"] > 0
        period = f"so far in {month_name(latest)} than in the same days of {month_name(c['prev_month'])}" if c["partial"] \
            else f"in {month_name(latest)} than in {month_name(c['prev_month'])}"
        out.append(("warn" if more else "ok", f"You spent {abs(c['change']):.0f}% {'more' if more else 'less'} {period}."))

    in_latest = [r for r in rs if r["date"].startswith(latest)]
    by_cat = defaultdict(float)
    for r in in_latest:
        by_cat[r["category"] or "Other"] += r["total"]
    top_cat, top_amt = max(by_cat.items(), key=lambda kv: kv[1])
    if len(by_cat) > 1:
        out.append(("info", f"{top_cat} was your biggest category in {month_name(latest)}: {inr(top_amt)}, "
                            f"{top_amt / by_month[latest] * 100:.0f}% of that month."))

    biggest = max(rs, key=lambda r: r["total"])
    out.append(("info", f"Your largest bill was {inr(biggest['total'])} at {biggest['merchant'] or 'an unknown merchant'} on {biggest['date']}."))

    taxed = [r for r in rs if r.get("tax")]
    if taxed:
        by_service = defaultdict(float)
        for r in taxed:
            by_service[r.get("service_type") or "other"] += r["tax"]
        service, amt = max(by_service.items(), key=lambda kv: kv[1])
        out.append(("info", f"You've paid {inr(sum(r['tax'] for r in taxed))} in GST across {len(taxed)} bill{'s' if len(taxed) != 1 else ''}, "
                            f"most of it ({inr(amt)}) on {gst.SERVICES.get(service, gst.SERVICES['other'])[0]}."))

    visits = defaultdict(list)
    for r in rs:
        if r["merchant"]:
            visits[r["merchant"]].append(r["total"])
    regular = max(visits.items(), key=lambda kv: len(kv[1]), default=None)
    if regular and len(regular[1]) >= 3:
        out.append(("info", f"You're a regular at {regular[0]}: {len(regular[1])} visits, {inr(sum(regular[1]) / len(regular[1]))} on average."))
    return [{"level": lvl, "message": msg} for lvl, msg in out]


if __name__ == "__main__":
    r = lambda d, t, cat="Food & Dining", m="Cafe", tax=None, svc=None: {
        "date": d, "total": t, "category": cat, "merchant": m, "tax": tax, "service_type": svc}
    data = [r("2026-08-03", 1000), r("2026-09-02", 600), r("2026-09-10", 900, "Shopping", "Mall", 162, "clothing"), r("2026-09-12", 100)]
    msgs = [i["message"] for i in insights(data, budget=2000, today=date(2026, 9, 15))]
    assert msgs[0].startswith("At this pace you'll spend about ₹3,200"), msgs  # 1600 in 15 of 30 days
    assert "60% more so far in Sep 2026 than in the same days of Aug 2026" in msgs[1], msgs
    assert msgs[2] == "Shopping was your biggest category in Sep 2026: ₹900, 56% of that month.", msgs
    assert "₹162 in GST across 1 bill," in msgs[4] and "Clothing" in msgs[4], msgs
    gap = [i["message"] for i in insights([r("2025-01-05", 80), r("2025-03-05", 2000, "Travel")], today=date(2026, 9, 15))]
    assert not any("more in" in x or "biggest category" in x for x in gap), gap  # Jan vs Mar: no comparison
    jan = [i["message"] for i in insights([r("2025-12-05", 100), r("2026-01-05", 200)], today=date(2026, 1, 20))]
    assert any("100% more so far in Jan 2026 than in the same days of Dec 2025" in x for x in jan), jan  # year boundary
    # the dashboard case: on 28 Sep, Aug 29-31 (rent + bills) must not count against September
    aug_sep = [r("2026-08-10", 1000), r("2026-08-30", 5000), r("2026-09-10", 1000)]
    c = compare_months(aug_sep, date(2026, 9, 28))
    assert (c["prev_spent"], c["change"], c["partial"], c["through_day"]) == (1000, 0, True, 28), c
    c = compare_months(aug_sep, date(2026, 10, 5))  # September finished: whole months
    assert (c["prev_spent"], c["partial"]) == (6000, False) and round(c["change"]) == -83, c
    assert "regular at Cafe: 3 visits" in msgs[5], msgs
    assert insights([], 1000) == []
    print("insights ok")
