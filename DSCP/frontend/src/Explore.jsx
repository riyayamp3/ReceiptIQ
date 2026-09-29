import { useEffect, useState } from "react";
import { api, inr, monthLabel } from "./api";
import { BarList, Columns, Diverging, Insights, Panel, Tile } from "./charts";

const fullMonth = (ym) => new Date(`${ym}-01T00:00`).toLocaleString("en-IN", { month: "long", year: "numeric" });

export default function Explore({ params, go }) {
  const { category, merchant, month } = params;
  const [a, setA] = useState(null);
  const [error, setError] = useState(null);
  const [day, setDay] = useState(null); // clicking a day filters the transaction list

  useEffect(() => {
    setA(null);
    setDay(null);
    setError(null);
    const q = new URLSearchParams(Object.entries({ category, merchant, month }).filter(([, v]) => v));
    api(`/analysis?${q}`).then(setA).catch((e) => setError(e.message));
  }, [category, merchant, month]);

  // drilling keeps the other filters; a merchant implies its category, so that filter becomes redundant
  const drill = (patch) => go("explore", { ...params, ...patch });
  const without = (key) => go("explore", { ...params, [key]: undefined });

  const title = merchant ?? category ?? (month ? fullMonth(month) : "All your spending");
  const chips = [
    category && ["category", category],
    merchant && ["merchant", merchant],
    month && ["month", fullMonth(month)],
  ].filter(Boolean);

  return (
    <div className="space-y-6">
      <div>
        <nav aria-label="Filters" className="flex flex-wrap items-center gap-2 text-sm">
          <button className="eyebrow hover:text-ink" onClick={() => go("explore", {})}>◈ Explore</button>
          {chips.map(([key, label]) => (
            <span key={key} className="flex items-center gap-1 rounded-full bg-sand py-0.5 pr-1 pl-3">
              {label}
              <button aria-label={`Remove ${key} filter`} className="grid size-5 place-items-center rounded-full hover:bg-paper" onClick={() => without(key)}>
                ✕
              </button>
            </span>
          ))}
        </nav>
        <h2 className="mt-2 text-3xl font-normal tracking-tight">{title}</h2>
        {month && !merchant && category && <p className="mt-1 text-sm text-muted">in {fullMonth(month)}</p>}
      </div>

      {error && <p className="text-warn">✕ {error}</p>}
      {!a && !error && <p className="text-muted">Loading…</p>}
      {a && a.summary.count === 0 && (
        <p className="rounded-2xl bg-sand p-10 text-center text-muted">No confirmed receipts match these filters.</p>
      )}
      {a && a.summary.count > 0 && <Body a={a} params={params} drill={drill} day={day} setDay={setDay} />}
    </div>
  );
}

function Body({ a, params, drill, day, setDay }) {
  const { category, merchant, month } = params;
  const s = a.summary;
  const receipts = day ? a.receipts.filter((r) => r.date === day) : a.receipts;
  const skipped = [a.monthly_ignored?.length && "monthly bills", a.outliers_ignored && `${a.outliers_ignored} unusually large bills`].filter(Boolean);

  return (
    <>
      <section className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Tile label="Spent" value={inr(s.total)} sub={s.share != null && s.share < 99.5 ? `${s.share.toFixed(1)}% of ${month ? "the month" : "all spending"}` : null} />
        <Tile label="Bills" value={s.count.toLocaleString("en-IN")} sub={`${s.first} → ${s.last}`} />
        <Tile label="Average bill" value={inr(s.avg)} />
        <Tile label="GST paid" value={inr(s.tax)} sub={s.total ? `${((s.tax / s.total) * 100).toFixed(1)}% of spend` : null} />
      </section>

      <Insights items={a.insights} title="What stands out" />

      <section className="grid gap-3 lg:grid-cols-2">
        {a.by_month && (
          <Panel title="Month by month" hint="Click a month to open it">
            <Columns
              rows={a.by_month.map((m) => ({ key: m.month, label: fullMonth(m.month), short: monthLabel(m.month), value: m.total }))}
              onSelect={(r) => drill({ month: r.key })}
            />
          </Panel>
        )}
        {a.by_day && (
          <Panel title="Day by day" hint={day ? "Click again to show all days" : "Click a day to see its bills"}>
            <Columns
              rows={daysOf(month).map((d) => ({ key: d, label: new Date(`${d}T00:00`).toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" }), short: String(+d.slice(8)), value: a.by_day.find((x) => x.date === d)?.total ?? 0 }))}
              onSelect={(r) => setDay(day === r.key ? null : r.key)}
              activeKey={day}
            />
          </Panel>
        )}
        <Panel title="Average by weekday" hint={skipped.length ? `Not counting ${skipped.join(" or ")}` : "Spend per calendar day"}>
          <Columns rows={a.by_weekday.map((d) => ({ key: d.day, label: d.day, value: d.avg }))} height={140} />
        </Panel>
      </section>

      <section className="grid gap-3 lg:grid-cols-2">
        {a.by_category && (
          <Panel title="Categories" hint="Click to explore">
            <BarList
              rows={a.by_category.map((c) => ({ key: c.name, label: c.name, value: c.total, sub: `${c.count}` }))}
              onSelect={(r) => drill({ category: r.key })}
            />
          </Panel>
        )}
        {a.changes && a.changes.rows.length > 0 && (
          <Panel
            title={`What changed vs ${monthLabel(a.changes.prev_month)}`}
            hint={a.changes.through_day ? `Days 1–${a.changes.through_day} of both months` : "Whole months"}
          >
            <Diverging
              rows={a.changes.rows.map((r) => ({ key: r.name, label: r.name, ...r }))}
              onSelect={(r) => drill(category ? { merchant: r.key } : { category: r.key })}
            />
          </Panel>
        )}
        {a.by_merchant && (
          <Panel title="Merchants" hint="Click to explore">
            <BarList
              rows={a.by_merchant.map((m) => ({ key: m.name, label: m.name, value: m.total, sub: `${m.visits}×` }))}
              onSelect={(r) => drill({ merchant: r.key })}
            />
          </Panel>
        )}
        <Panel title="Most bought">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-muted">
              <tr>
                <th className="pb-2 font-normal">Item</th>
                <th className="pb-2 text-right font-normal">Bills</th>
                <th className="pb-2 text-right font-normal">Spent</th>
              </tr>
            </thead>
            <tbody>
              {a.top_items.map((it) => (
                <tr key={it.name} className="border-t border-line">
                  <td className="py-2">{it.name}</td>
                  <td className="py-2 text-right tabular-nums">{it.bills}</td>
                  <td className="py-2 text-right tabular-nums">{inr(it.spent)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
        <Panel title="How you paid">
          <BarList rows={a.by_payment.map((p) => ({ key: p.name, label: p.name, value: p.total, sub: `${p.count}` }))} />
        </Panel>
      </section>

      <Panel title={day ? `Bills on ${new Date(`${day}T00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "long" })}` : "Bills"} hint={a.receipts.length === 100 && !day ? "Latest 100" : null}>
        <div className="max-h-96 overflow-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-paper text-left text-xs text-muted">
              <tr>
                <th className="py-2 font-normal">Date</th>
                <th className="py-2 font-normal">Merchant</th>
                {!category && <th className="py-2 font-normal">Category</th>}
                <th className="py-2 text-right font-normal">Total</th>
              </tr>
            </thead>
            <tbody>
              {receipts.map((r) => (
                <tr key={r.id} className="border-t border-line">
                  <td className="py-2 whitespace-nowrap tabular-nums">{r.date}</td>
                  <td className="py-2">
                    {merchant ? r.merchant : <button className="text-left underline decoration-line underline-offset-4 hover:decoration-ink" onClick={() => drill({ merchant: r.merchant })}>{r.merchant}</button>}
                  </td>
                  {!category && (
                    <td className="py-2">
                      <button className="text-left text-muted hover:text-ink" onClick={() => drill({ category: r.category })}>{r.category}</button>
                    </td>
                  )}
                  <td className="py-2 text-right tabular-nums">{inr(r.total)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </>
  );
}

function daysOf(ym) {
  const [y, m] = ym.split("-").map(Number);
  const n = new Date(y, m, 0).getDate();
  return Array.from({ length: n }, (_, i) => `${ym}-${String(i + 1).padStart(2, "0")}`);
}
