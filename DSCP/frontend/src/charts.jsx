// Small HTML charts. Every clickable mark is a real <button>, so drill-down works by mouse, keyboard and screen reader.
import { inr } from "./api";

export function Panel({ title, hint, children, className = "" }) {
  return (
    <div className={`rounded-2xl p-5 ring-1 ring-line ${className}`}>
      <div className="mb-4 flex items-baseline justify-between gap-3">
        <h3 className="text-lg">{title}</h3>
        {hint && <span className="text-xs text-muted">{hint}</span>}
      </div>
      {children}
    </div>
  );
}

export function Tile({ label, value, sub }) {
  return (
    <div className="rounded-2xl bg-sand p-5">
      <p className="text-xs text-muted">{label}</p>
      <p className="mt-2 text-2xl tracking-tight tabular-nums">{value}</p>
      {sub && <p className="mt-1 text-xs text-muted">{sub}</p>}
    </div>
  );
}

/** Vertical columns (months, days, weekdays). rows: [{key, label, short?, value}] */
export function Columns({ rows, onSelect, activeKey, format = inr, height = 180 }) {
  const max = Math.max(...rows.map((r) => r.value), 1);
  const every = Math.ceil(rows.length / 12); // thin the axis labels when there are many columns
  return (
    <div>
      <p className="mb-1 text-[11px] text-muted tabular-nums">{format(max)}</p>
      <div className="flex items-end gap-[3px] border-t border-b border-dashed border-line" style={{ height }}>
        {rows.map((r) => {
          const El = onSelect ? "button" : "div";
          const text = `${r.label}: ${format(r.value)}`;
          return (
            <El
              key={r.key}
              onClick={onSelect && (() => onSelect(r))}
              aria-label={onSelect ? `${text}. Explore` : text}
              className={`group relative flex h-full min-w-0 flex-1 items-end rounded-t-[4px] outline-offset-2 ${onSelect ? "cursor-pointer" : ""}`}
            >
              <span
                className={`w-full rounded-t-[4px] transition-colors ${r.key === activeKey ? "bg-lime" : onSelect ? "bg-ink group-hover:bg-ink-soft" : "bg-ink"}`}
                style={{ height: `${(r.value / max) * 100}%`, minHeight: r.value ? 2 : 0 }}
              />
              <span className="pointer-events-none absolute bottom-full left-1/2 z-10 mb-1 hidden -translate-x-1/2 rounded-md bg-ink px-2 py-1 text-xs whitespace-nowrap text-paper group-hover:block group-focus-visible:block">
                {text}
              </span>
            </El>
          );
        })}
      </div>
      <div className="mt-1 flex gap-[3px] text-[10px] text-muted" aria-hidden>
        {rows.map((r, i) => (
          <span key={r.key} className="min-w-0 flex-1 overflow-visible text-center whitespace-nowrap">
            {i % every === 0 ? (r.short ?? r.label) : ""}
          </span>
        ))}
      </div>
    </div>
  );
}

/** Horizontal bars with direct labels. rows: [{key, label, value, sub?}] */
export function BarList({ rows, onSelect, format = inr }) {
  const max = Math.max(...rows.map((r) => r.value), 1);
  return (
    <ul className="space-y-0.5">
      {rows.map((r) => {
        const El = onSelect ? "button" : "div";
        return (
          <li key={r.key}>
            <El
              onClick={onSelect && (() => onSelect(r))}
              className={`group grid w-full grid-cols-[minmax(0,10rem)_1fr_auto] items-center gap-3 rounded-lg px-2 py-1.5 text-left text-sm ${onSelect ? "cursor-pointer hover:bg-sand" : ""}`}
            >
              <span className="truncate">{r.label}</span>
              <span className="h-2 rounded-full bg-sand group-hover:bg-paper">
                <span className="block h-2 rounded-full bg-ink" style={{ width: `${(r.value / max) * 100}%` }} />
              </span>
              <span className="text-right tabular-nums">
                {format(r.value)}
                {r.sub && <span className="ml-2 text-xs text-muted">{r.sub}</span>}
              </span>
            </El>
          </li>
        );
      })}
    </ul>
  );
}

/** Increase/decrease bars around a centre line. rows: [{key, label, delta, before, now}] */
export function Diverging({ rows, onSelect }) {
  const max = Math.max(...rows.map((r) => Math.abs(r.delta)), 1);
  return (
    <ul className="space-y-0.5">
      {rows.map((r) => {
        const El = onSelect ? "button" : "div";
        const w = `${(Math.abs(r.delta) / max) * 100}%`;
        const up = r.delta > 0;
        return (
          <li key={r.key}>
            <El
              onClick={onSelect && (() => onSelect(r))}
              aria-label={`${r.label}: ${up ? "up" : "down"} ${inr(Math.abs(r.delta))}, from ${inr(r.before)} to ${inr(r.now)}`}
              className={`grid w-full grid-cols-[minmax(0,9rem)_1fr_1fr_auto] items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm ${onSelect ? "cursor-pointer hover:bg-sand" : ""}`}
            >
              <span className="truncate">{r.label}</span>
              <span className="flex h-2 justify-end border-r border-muted/40">
                {!up && <span className="h-2 rounded-l-full bg-ink" style={{ width: w }} />}
              </span>
              <span className="flex h-2">{up && <span className="h-2 rounded-r-full bg-warn" style={{ width: w }} />}</span>
              <span className={`text-right tabular-nums ${up ? "text-warn" : ""}`}>
                {up ? "+" : "−"}
                {inr(Math.abs(r.delta))}
              </span>
            </El>
          </li>
        );
      })}
    </ul>
  );
}

export function Insights({ items, title = "Insights" }) {
  if (!items?.length) return null;
  return (
    <div className="rounded-2xl p-5 ring-1 ring-line">
      <h3 className="text-lg">{title}</h3>
      <ul className="mt-3 space-y-2 text-sm">
        {items.map((i, k) => (
          <li key={k} className={`flex gap-2 ${i.level === "warn" ? "text-warn" : ""}`}>
            <span aria-hidden>{{ ok: "✓", warn: "⚠", info: "•" }[i.level]}</span>
            {i.message}
          </li>
        ))}
      </ul>
    </div>
  );
}
