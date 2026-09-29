export async function api(path, opts = {}) {
  const json = opts.json !== undefined;
  const res = await fetch(`/api${path}`, {
    ...opts,
    ...(json && { body: JSON.stringify(opts.json), headers: { "Content-Type": "application/json" } }),
  });
  const body = await res.json().catch(() => null);
  // session expired mid-use: App listens and shows the login screen
  if (res.status === 401 && path !== "/me" && !path.startsWith("/auth")) window.dispatchEvent(new Event("logged-out"));
  if (!res.ok) throw new Error(typeof body?.detail === "string" ? body.detail : "Request failed");
  return body;
}

export const inr = (n) => (n == null ? "—" : "₹" + Math.round(n).toLocaleString("en-IN"));

export const monthLabel = (ym) =>
  new Date(`${ym}-01T00:00`).toLocaleString("en-IN", { month: "short", year: "2-digit" });
