import { useEffect, useState } from "react";
import { api } from "./api";

export default function Profile({ me, onSaved, onLogout }) {
  const [form, setForm] = useState({ name: me.name, monthly_budget: me.monthly_budget ?? "" });
  const [status, setStatus] = useState(null);
  const [groups, setGroups] = useState([]);
  const [activeGroup, setActiveGroup] = useState(null);
  const [newGroupName, setNewGroupName] = useState("");
  const [joinCode, setJoinCode] = useState("");

  useEffect(() => {
    refreshGroups();
  }, []);

  async function refreshGroups() {
    try {
      const gs = await api("/groups");
      setGroups(gs);
      const active = await api("/groups/active");
      setActiveGroup(active);
    } catch {}
  }

  async function save(e) {
    e.preventDefault();
    setStatus(null);
    try {
      const budget = form.monthly_budget === "" ? null : Number(form.monthly_budget);
      onSaved(await api("/me", { method: "PUT", json: { name: form.name, monthly_budget: budget } }));
      setStatus({ ok: true, text: "Saved" });
    } catch (err) { setStatus({ ok: false, text: err.message }); }
  }

  async function createGroup(e) {
    e.preventDefault();
    try {
      await api("/groups", { method: "POST", json: { name: newGroupName } });
      setNewGroupName("");
      setStatus({ ok: true, text: "Group created" });
      refreshGroups();
    } catch (err) { setStatus({ ok: false, text: err.message }); }
  }

  async function joinGroup(e) {
    e.preventDefault();
    try {
      await api("/groups/join", { method: "POST", json: { join_code: joinCode } });
      setJoinCode("");
      setStatus({ ok: true, text: "Joined group" });
      refreshGroups();
    } catch (err) { setStatus({ ok: false, text: err.message }); }
  }

  async function switchGroup(gid) {
    try {
      await api("/groups/active", { method: "PUT", json: { group_id: gid } });
      setStatus({ ok: true, text: "Switched group" });
      refreshGroups();
    } catch (err) { setStatus({ ok: false, text: err.message }); }
  }

  return (
    <div className="mx-auto max-w-xl">
      <p className="eyebrow">Your profile</p>
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
          <span className="mb-1 block text-xs text-muted">Monthly budget (Rs) - used for budget tracker and alerts</span>
          <input className="field" type="number" min="0" step="100" placeholder="e.g. 20000" value={form.monthly_budget} onChange={(e) => setForm({ ...form, monthly_budget: e.target.value })} />
        </label>
        <div className="flex items-center justify-between gap-3">
          <span className={"text-sm " + (status?.ok ? "text-ink" : "text-warn")} role="status">{status?.text}</span>
          <button className="btn">Save</button>
        </div>
      </form>

      <section className="mt-6 rounded-2xl bg-sand p-6">
        <p className="eyebrow">Your groups</p>
        <p className="mt-1 text-sm text-muted">Create groups for family, friends, trips, roommates - anything. Each group has its own receipts and analytics.</p>

        {groups.length > 0 && (
          <ul className="mt-4 space-y-2">
            {groups.map((g) => (
              <li key={g.id} className={"flex items-center justify-between rounded-xl px-4 py-3 " + (activeGroup?.id === g.id ? "bg-ink text-paper" : "bg-paper")}>
                <div>
                  <p className="font-medium">{g.name}</p>
                  <p className={"text-xs " + (activeGroup?.id === g.id ? "text-paper/70" : "text-muted")}>Code: {g.join_code}</p>
                </div>
                {activeGroup?.id !== g.id && (
                  <button className="text-sm underline" onClick={() => switchGroup(g.id)}>Switch to this</button>
                )}
                {activeGroup?.id === g.id && <span className="text-xs">Active</span>}
              </li>
            ))}
          </ul>
        )}

        {activeGroup && activeGroup.members && (
          <div className="mt-4">
            <p className="text-sm text-muted">Members of {activeGroup.name}:</p>
            <ul className="mt-1 space-y-0.5">
              {activeGroup.members.map((m) => (
                <li key={m.id} className="text-sm">
                  {m.name} {m.email === me.email && <span className="text-muted">(you)</span>}
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="mt-4 flex flex-wrap gap-2">
          <form onSubmit={createGroup} className="flex items-center gap-2">
            <input className="field" placeholder="New group name (e.g. Goa Trip, Flatmates...)" value={newGroupName} onChange={(e) => setNewGroupName(e.target.value)} />
            <button className="btn" type="submit">Create</button>
          </form>
          <form onSubmit={joinGroup} className="flex items-center gap-2">
            <input className="field" placeholder="Join code" value={joinCode} onChange={(e) => setJoinCode(e.target.value)} />
            <button className="btn-outline" type="submit">Join</button>
          </form>
        </div>
      </section>

      <button className="btn-outline mt-4" onClick={onLogout}>Log out</button>
    </div>
  );
}
