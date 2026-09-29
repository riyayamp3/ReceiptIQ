import { useState } from "react";
import { api } from "./api";

export default function Auth({ onAuthed }) {
  const [mode, setMode] = useState("login");
  const [form, setForm] = useState({ name: "", email: "", password: "" });
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const signup = mode === "signup";
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  async function submit(e) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      onAuthed(await api(`/auth/${mode}`, { method: "POST", json: form }));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-[70vh] place-items-center">
      <div className="w-full max-w-md rounded-2xl bg-sand px-6 py-10 sm:px-10">
        <p className="eyebrow justify-center">◈ Receipt intelligence</p>
        <h1 className="mt-4 text-center text-3xl font-normal tracking-tight">
          {signup ? "Create your account" : "Welcome back"}
        </h1>
        <p className="mt-2 text-center text-sm text-muted">
          {signup ? "Your receipts and insights stay private to you." : "Log in to see your receipts and spending."}
        </p>

        <form onSubmit={submit} className="mt-8 space-y-4">
          {signup && (
            <label className="block">
              <span className="mb-1 block text-xs text-muted">Name</span>
              <input className="field" autoComplete="name" required value={form.name} onChange={set("name")} />
            </label>
          )}
          <label className="block">
            <span className="mb-1 block text-xs text-muted">Email</span>
            <input className="field" type="email" autoComplete="email" required value={form.email} onChange={set("email")} />
          </label>
          <label className="block">
            <span className="mb-1 block text-xs text-muted">Password{signup && " (at least 8 characters)"}</span>
            <input
              className="field"
              type="password"
              autoComplete={signup ? "new-password" : "current-password"}
              minLength={signup ? 8 : undefined}
              required
              value={form.password}
              onChange={set("password")}
            />
          </label>
          {error && <p className="text-sm text-warn" role="alert">✕ {error}</p>}
          <button className="btn w-full" disabled={busy}>
            {busy ? "Please wait…" : signup ? "Create account" : "Log in"}
          </button>
        </form>

        <p className="mt-6 text-center text-sm text-muted">
          {signup ? "Already have an account?" : "New here?"}{" "}
          <button className="text-ink underline underline-offset-4" onClick={() => (setMode(signup ? "login" : "signup"), setError(null))}>
            {signup ? "Log in" : "Create an account"}
          </button>
        </p>
      </div>
    </div>
  );
}
