import { useEffect, useState } from "react";
import { api, inr, monthLabel } from "./api";
import { BarList, Columns, Insights, Panel, Tile } from "./charts";
import { MemberFilter } from "./MemberFilter";

export default function Dashboard({ me, go }) {
  const [s, setS] = useState(null);
  const [error, setError] = useState(null);
  const [member, setMember] = useState(me?.id ?? null);
  const [roster, setRoster] = useState([]);

  useEffect(() => {
    api("/groups/roster").then(setRoster).catch(() => setRoster(me ? [me] : []));
  }, []);

  useEffect(() => {
    setS(null);
    setError(null);
    const q = member != null ? `?member=${member}` : "";
    api(`/stats${q}`).then(setS).catch((e) => setError(e.message));
  }, [member]);

  if (error) return <p className="text-warn">x {error}</p>;
  if (!s) return <p className="text-muted">Loading...</p>;

  const hour = new Date().getHours();
  const greeting = `${hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening"}, ${me.name.split(" ")[0]}`;

  const header = (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div>
        <p className="eyebrow">Your spending</p>
        <h2 className="mt-2 text-3xl font-normal tracking-tight">{greeting}</h2>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        {roster.length > 1 && (
          <label className="flex items-center gap-2 text-sm">
            <span className="text-muted">Member</span>
            <MemberFilter roster={roster} value={member} onChange={setMember} me={me} />
          </label>
        )}
        {s.summary.count > 0 && (
          <button className="btn-outline" onClick={() => go("explore", {})}>
            Explore all spending
          </button>
        )}
      </div>
    </div>
  );

  if (s.summary.count === 0)
    return (
      <div className="space-y-6">
        {header}
        <p className="rounded-2xl bg-sand p-10 text-center text-muted">No confirmed receipts yet. Upload and confirm a few to see your dashboard.</p>
      </div>
    );

  const c = s.month_compare;
  const prevLabel = c?.partial
    ? `${new Date(`${c.prev_month}-01T00:00`).toLocaleString("en-IN", { month: "short" })} 1-${c.through_day}`
    : c && monthLabel(c.prev_month);

  return (
    <div className="space-y-8">
      {header}
      <section className="grid gap-3 lg:grid-cols-[1fr_1.4fr]">
        <Budget b={s.budget} onProfile={() => go("profile")} />
        <Insights items={s.insights} title="Insights for you" />
      </section>

      <section className="grid gap-3 lg:grid-cols-[1.3fr_1fr_1fr]">
        <button
          className="rounded-2xl bg-sand p-6 text-left transition hover:ring-1 hover:ring-ink/30 lg:row-span-2"
          onClick={() => c && go("explore", { month: c.month })}
        >
          <p className="eyebrow">
            {c ? monthLabel(c.month) : "Latest month"} spending{c?.partial && ` - so far (1-${c.through_day})`}
          </p>
          <p className="mt-4 text-5xl font-light tracking-tight">{inr(c?.spent)}</p>
          {c?.change != null && (
            <p className="mt-3 text-sm text-muted">
              <span className="text-ink">{c.change >= 0 ? "up" : "down"} {Math.abs(c.change).toFixed(1)}%</span> vs {prevLabel} ({inr(c.prev_spent)})
            </p>
          )}
          <p className="mt-4 text-sm underline underline-offset-4">See what changed</p>
        </button>
        <Tile label="Total spent" value={inr(s.summary.total)} />
        <Tile label="Receipts" value={s.summary.count.toLocaleString("en-IN")} />
        <Tile label="Average receipt" value={inr(s.summary.avg)} />
        <Tile label="GST paid" value={inr(s.summary.tax)} />
      </section>

      <section className="grid gap-3 lg:grid-cols-2">
        <Panel title="Monthly spending" hint="Click a month to open it">
          <Columns
            rows={s.by_month.map((m) => ({ key: m.month, label: monthLabel(m.month), value: m.total }))}
            onSelect={(r) => go("explore", { month: r.key })}
            height={220}
          />
        </Panel>
        <Panel title="By category" hint="Click to explore">
          <BarList
            rows={s.by_category.map((x) => ({ key: x.name, label: x.name, value: x.total, sub: `${Math.round((x.total / s.summary.total) * 100)}%` }))}
            onSelect={(r) => go("explore", { category: r.key })}
          />
        </Panel>
      </section>

      <Panel title="Top merchants" hint="Click to explore">
        <BarList
          rows={s.by_merchant.map((m) => ({ key: m.name, label: m.name ?? "Unknown", value: m.total, sub: `${m.visits}x - avg ${inr(m.avg)}` }))}
          onSelect={(r) => r.key && go("explore", { merchant: r.key })}
        />
      </Panel>
    </div>
  );
}

function Budget({ b, onProfile }) {
  if (!b.budget)
    return (
      <div className="rounded-2xl bg-sand p-6">
        <p className="eyebrow">Monthly budget</p>
        <p className="mt-4 text-sm text-muted">Set a monthly budget to see how this month is going and get alerts before you overspend.</p>
        <button className="btn mt-4" onClick={onProfile}>Set a budget</button>
      </div>
    );
  const pct = (b.spent / b.budget) * 100;
  const over = b.spent > b.budget;
  return (
    <div className="rounded-2xl bg-sand p-6">
      <p className="eyebrow">{monthLabel(b.month)} budget</p>
      <p className="mt-4 text-4xl font-light tracking-tight tabular-nums">
        {inr(b.spent)} <span className="text-base text-muted">of {inr(b.budget)}</span>
      </p>
      <div
        className="mt-4 h-2 overflow-hidden rounded-full bg-paper"
        role="progressbar"
        aria-valuenow={Math.round(pct)}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div className={`h-full rounded-full ${over ? "bg-warn" : "bg-ink"}`} style={{ width: `${Math.min(pct, 100)}%` }} />
      </div>
      <p className="mt-3 text-sm text-muted">
        {over ? <span className="text-warn">{inr(b.spent - b.budget)} over budget</span> : `${inr(b.budget - b.spent)} left`}
        {" - "}
        {b.days_left} days to go
        {b.spent > 0 && ` - on pace for ${inr(b.projected)}`}
      </p>
    </div>
  );
}
