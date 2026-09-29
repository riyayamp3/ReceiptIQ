import { useState } from "react";
import { api } from "./api";

export default function Profile({ me, onSaved, onLogout }) {
  const [form, setForm] = useState({ name: me.name, monthly_budget: me.monthly_budget ?? "" });
  const [status, setStatus] = useState(null);

  async function save(e) {
    e.preventDefault();
    setStatus(null);
    try {
      const budget = form.monthly_budget === "" ? null : Number(form.monthly_budget);
      onSaved(await api("/me", { method: "PUT", json: { name: form.name, monthly_budget: budget } }));
      setStatus({ ok: true, text: "✓ Saved" });
    } catch (err) {
      setStatus({ ok: false, text: "✕ " + err.message });
    }
  }

  return (
    <div className="mx-auto max-w-xl">
      <p className="eyebrow">◈ Your profile</p>
      <h2 className="mt-2 text-3xl font-normal tracking-tight">Make it yours</h2>
      <form onSubmit={save} className="mt-6 space-y-4 rounded-2xl bg-sand p-6">
        <label className="block">
          <span className="mb-1 block text-xs text-muted">Name</span>
          <input className="field" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </label>
        <label className="block">
          <span className="mb-1 block text-xs text-muted">Email</span>
          <input className="field text-muted" value={me.email} disabled />
        </label>
        <label className="block">
          <span className="mb-1 block text-xs text-muted">Monthly budget (₹) — used for your budget tracker and alerts</span>
          <input
            className="field"
            type="number"
            min="0"
            step="100"
            placeholder="e.g. 20000"
            value={form.monthly_budget}
            onChange={(e) => setForm({ ...form, monthly_budget: e.target.value })}
          />
        </label>
        <div className="flex items-center justify-between gap-3">
          <span className={`text-sm ${status?.ok ? "text-ink" : "text-warn"}`} role="status">{status?.text}</span>
          <button className="btn">Save</button>
        </div>
      </form>
      <button className="btn-outline mt-4" onClick={onLogout}>Log out</button>
    </div>
  );
}
