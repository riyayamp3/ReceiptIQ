import { useEffect, useState } from "react";
import { api, inr } from "./api";

const PAYMENTS = ["UPI", "Card", "Cash", "Other"];
const PAGE = 30; // demo accounts have 1000+ receipts: render a page at a time
const num = (v) => (v === "" || v == null ? null : Number(v));

export default function Review({ receipts, setReceipts }) {
  const [show, setShow] = useState("pending");
  const [limit, setLimit] = useState(PAGE);
  const [categories, setCategories] = useState([]);
  const [services, setServices] = useState([]);
  useEffect(() => {
    api("/categories").then(setCategories);
    api("/service-types").then(setServices);
  }, []);

  const list = receipts.filter((r) => r.status === show);
  const replace = (r) => setReceipts((rs) => rs.map((x) => (x.id === r.id ? r : x)));
  const remove = (id) => setReceipts((rs) => rs.filter((x) => x.id !== id));

  return (
    <div>
      <p className="eyebrow">◈ Human in the loop</p>
      <div className="mt-2 flex flex-wrap items-end justify-between gap-4">
        <h2 className="text-3xl font-normal tracking-tight">Check what we read</h2>
        <div className="flex rounded-full bg-sand p-1 text-sm">
          {["pending", "confirmed"].map((s) => (
            <button
              key={s}
              onClick={() => (setShow(s), setLimit(PAGE))}
              className={`rounded-full px-4 py-1 capitalize ${show === s ? "bg-ink text-paper" : ""}`}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      {list.length === 0 && (
        <p className="mt-10 rounded-2xl bg-sand p-10 text-center text-muted">
          {show === "pending" ? "Nothing to review. Upload some receipts first." : "No confirmed receipts yet."}
        </p>
      )}
      <div className="mt-6 space-y-5">
        {list.slice(0, limit).map((r) => (
          <ReceiptCard key={r.id} receipt={r} categories={categories} services={services} onSaved={replace} onDeleted={remove} />
        ))}
      </div>
      {list.length > limit && (
        <div className="mt-6 text-center">
          <button className="btn-outline" onClick={() => setLimit(limit + PAGE)}>
            Show more ({list.length - limit} left)
          </button>
        </div>
      )}
    </div>
  );
}

function ReceiptCard({ receipt, categories, services, onSaved, onDeleted }) {
  const [r, setR] = useState(receipt);
  const [error, setError] = useState(null);
  const set = (k) => (e) => setR({ ...r, [k]: e.target.value });
  const setItem = (i, k, v) => setR({ ...r, items: r.items.map((it, j) => (j === i ? { ...it, [k]: v } : it)) });

  const itemsSum = r.items.reduce((s, it) => s + (Number(it.price) || 0), 0);
  const expected = num(r.subtotal) ?? num(r.total);
  const mismatch = r.items.length > 0 && expected != null && Math.abs(itemsSum - expected) > 1;
  const canConfirm = r.merchant && r.date && num(r.total) != null && r.category;

  async function save(status) {
    setError(null);
    try {
      const body = {
        ...r,
        status,
        subtotal: num(r.subtotal),
        tax: num(r.tax),
        total: num(r.total),
        cgst: num(r.cgst),
        sgst: num(r.sgst),
        igst: num(r.igst),
        gst_rate: num(r.gst_rate),
        items: r.items.filter((it) => it.name && it.price !== "").map((it) => ({ ...it, qty: num(it.qty) ?? 1, price: num(it.price) })),
      };
      onSaved(await api(`/receipts/${r.id}`, { method: "PUT", json: body }));
    } catch (e) {
      setError(e.message);
    }
  }

  async function del() {
    if (!confirm("Delete this receipt?")) return;
    await api(`/receipts/${r.id}`, { method: "DELETE" });
    onDeleted(r.id);
  }

  // Sarvam's per-field confidence: flag what the reviewer should double-check
  const fc = r.field_confidence ?? {};
  const flag = (k) => receipt.status === "pending" && fc[k] != null && fc[k] < 0.9;
  const conf = r.ocr_confidence == null ? null : Math.round(r.ocr_confidence * 100);
  const LANG = { en: "English", hi: "हिंदी", mr: "मराठी" };

  return (
    <article className="grid gap-5 rounded-2xl bg-white/40 p-4 shadow-[0_1px_0_#d6d0bf] ring-1 ring-line md:grid-cols-[240px_1fr]">
      <a href={r.image ? `/api/images/${r.image}` : undefined} target="_blank" rel="noreferrer" className="relative block">
        {r.image ? (
          <img src={`/api/images/${r.image}`} alt="Receipt photo" className="h-64 w-full rounded-xl bg-sand object-contain md:h-full md:max-h-[420px]" />
        ) : (
          <div className="grid h-40 w-full place-items-center rounded-xl bg-sand text-sm text-muted md:h-full">No photo</div>
        )}
        <span className={`absolute top-2 left-2 rounded-full px-2.5 py-0.5 text-xs ${conf == null || conf >= 80 ? "bg-lime" : "bg-[#efc9a4]"}`}>
          {{ sarvam: "Sarvam Vision", easyocr: "EasyOCR", synthetic: "Demo data" }[r.engine] ?? "Manual"} · {LANG[r.lang] ?? "English"}
          {conf != null && ` · ${conf}%`}
        </span>
      </a>

      <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Merchant" flagged={flag("merchant")}><input className="field" value={r.merchant ?? ""} onChange={set("merchant")} /></Field>
          <Field label="Date" flagged={flag("date")}><input type="date" className="field" value={r.date ?? ""} onChange={set("date")} /></Field>
          <Field label="Category" flagged={flag("category")}>
            <select className="field" value={r.category ?? ""} onChange={set("category")}>
              <option value="" disabled>Choose…</option>
              {categories.map((c) => <option key={c}>{c}</option>)}
            </select>
          </Field>
          <Field label="Paid with" flagged={flag("payment_method")}>
            <select className="field" value={r.payment_method ?? ""} onChange={set("payment_method")}>
              <option value="">Unknown</option>
              {PAYMENTS.map((p) => <option key={p}>{p}</option>)}
            </select>
          </Field>
        </div>

        <div>
          <div className="grid grid-cols-[1fr_56px_96px_28px] gap-2 text-xs text-muted">
            <span>Item</span><span>Qty</span><span>Price (₹)</span>
          </div>
          {r.items.map((it, i) => (
            <div key={i} className="mt-1.5 grid grid-cols-[1fr_56px_96px_28px] gap-2">
              <input aria-label="Item name" className="field" value={it.name} onChange={(e) => setItem(i, "name", e.target.value)} />
              <input aria-label="Quantity" type="number" min="0" className="field" value={it.qty} onChange={(e) => setItem(i, "qty", e.target.value)} />
              <input aria-label="Price" type="number" step="0.01" className="field" value={it.price} onChange={(e) => setItem(i, "price", e.target.value)} />
              <button aria-label="Remove item" className="text-muted hover:text-warn" onClick={() => setR({ ...r, items: r.items.filter((_, j) => j !== i) })}>✕</button>
            </div>
          ))}
          <button className="mt-2 text-sm underline underline-offset-4" onClick={() => setR({ ...r, items: [...r.items, { name: "", qty: 1, price: "" }] })}>
            + Add item
          </button>
        </div>

        <div className="grid grid-cols-3 gap-3">
          <Field label="Subtotal" flagged={flag("subtotal")}><input type="number" step="0.01" className="field" value={r.subtotal ?? ""} onChange={set("subtotal")} /></Field>
          <Field label="GST / tax" flagged={flag("tax")}><input type="number" step="0.01" className="field" value={r.tax ?? ""} onChange={set("tax")} /></Field>
          <Field label="Total" flagged={flag("total")}><input type="number" step="0.01" className="field font-medium" value={r.total ?? ""} onChange={set("total")} /></Field>
        </div>

        <section className="rounded-xl bg-sand p-4">
          <h3 className="text-sm font-medium">GST</h3>
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <Field label="Service type" flagged={flag("service_type")}>
              <select className="field" value={r.service_type ?? "other"} onChange={set("service_type")}>
                {services.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
              </select>
            </Field>
            <Field label="Seller GSTIN" flagged={flag("gstin")}>
              <input className="field font-mono uppercase" value={r.gstin ?? ""} onChange={set("gstin")} />
            </Field>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[["gst_rate", "Rate %"], ["cgst", "CGST ₹"], ["sgst", "SGST ₹"], ["igst", "IGST ₹"]].map(([k, label]) => (
              <Field key={k} label={label} flagged={flag(k)}>
                <input type="number" step="0.01" className="field" value={r[k] ?? ""} onChange={set(k)} />
              </Field>
            ))}
          </div>
          {/* verdicts come from the server's GST rules, so they refresh after saving */}
          <ul className="mt-3 space-y-1.5 text-sm">
            {(receipt.gst_check ?? []).map((g, i) => (
              <li key={i} className={g.level === "warn" ? "text-warn" : g.level === "ok" ? "text-ink" : "text-muted"}>
                {{ ok: "✓", warn: "⚠", info: "ⓘ" }[g.level]} {g.message}
              </li>
            ))}
          </ul>
        </section>

        {mismatch && (
          <p className="text-sm text-warn">⚠ Items add up to {inr(itemsSum)} but the {num(r.subtotal) != null ? "subtotal" : "total"} is {inr(expected)}. Check for a misread line.</p>
        )}
        {error && <p className="text-sm text-warn">✕ {error}</p>}

        <details className="text-sm text-muted">
          <summary className="cursor-pointer">Raw extraction</summary>
          <pre className="mt-2 max-h-48 overflow-auto rounded-lg bg-sand p-3 font-mono text-xs whitespace-pre-wrap">{r.raw_text}</pre>
        </details>

        <div className="flex flex-wrap justify-end gap-2">
          <button className="btn-outline" onClick={del}>Delete</button>
          <button className="btn" disabled={!canConfirm} title={canConfirm ? "" : "Merchant, date, total and category are required"} onClick={() => save("confirmed")}>
            {receipt.status === "confirmed" ? "Save changes" : "Confirm"}
          </button>
        </div>
      </div>
    </article>
  );
}

function Field({ label, flagged, children }) {
  return (
    <label className={`block ${flagged ? "[&_.field]:border-warn" : ""}`}>
      <span className={`mb-1 block text-xs ${flagged ? "text-warn" : "text-muted"}`}>
        {label}
        {flagged && " · please check"}
      </span>
      {children}
    </label>
  );
}
