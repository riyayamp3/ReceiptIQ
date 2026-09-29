import { useEffect, useState } from "react";
import { api } from "./api";
import Auth from "./Auth";
import Upload from "./Upload";
import Review from "./Review";
import Dashboard from "./Dashboard";
import Explore from "./Explore";
import Profile from "./Profile";

const TABS = [["upload", "Upload"], ["review", "Review"], ["dashboard", "Dashboard"], ["explore", "Explore"]];
const PAGES = new Set([...TABS.map(([p]) => p), "profile"]);

// the page lives in the URL hash (#/explore?category=Groceries): back button and bookmarks work, no router needed
function readRoute() {
  const [path, query] = window.location.hash.replace(/^#\/?/, "").split("?");
  return { page: PAGES.has(path) ? path : "upload", params: Object.fromEntries(new URLSearchParams(query)) };
}

export function go(page, params = {}) {
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v)).toString();
  window.location.hash = `/${page}${q ? `?${q}` : ""}`;
}

export default function App() {
  const [me, setMe] = useState(undefined); // undefined = checking session, null = logged out
  const [route, setRoute] = useState(readRoute);
  const [receipts, setReceipts] = useState([]);

  useEffect(() => {
    api("/me").then(setMe).catch(() => setMe(null));
    const onLoggedOut = () => setMe(null);
    const onHash = () => (setRoute(readRoute()), window.scrollTo(0, 0));
    window.addEventListener("logged-out", onLoggedOut);
    window.addEventListener("hashchange", onHash);
    return () => (window.removeEventListener("logged-out", onLoggedOut), window.removeEventListener("hashchange", onHash));
  }, []);

  useEffect(() => {
    setReceipts([]);
    if (me?.id) api("/receipts").then(setReceipts).catch(() => {});
  }, [me?.id]);

  async function logout() {
    await api("/auth/logout", { method: "POST" }).catch(() => {});
    setMe(null);
    go("upload");
  }

  const { page, params } = route;
  const pending = receipts.filter((r) => r.status === "pending").length;

  return (
    <div className="min-h-screen p-2 sm:p-8">
      <div className="mx-auto max-w-6xl rounded-3xl bg-paper px-4 py-4 shadow-[0_2px_0_#c9c3b2] sm:px-8 sm:py-5">
        <header className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-lg">
            <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden>
              <path d="M5 3h14v18l-2.3-1.5L14.3 21 12 19.5 9.7 21l-2.4-1.5L5 21z M9 8h6 M9 12h6 M9 16h3" />
            </svg>
            <span className="hidden sm:inline">ReceiptIQ</span>
          </div>
          {me && (
            <>
              <nav className="flex gap-0.5 text-sm sm:gap-4">
                {TABS.map(([p, label]) => (
                  <a
                    key={p}
                    href={`#/${p}`}
                    aria-current={page === p ? "page" : undefined}
                    className={`rounded-full px-2 py-1 transition ${page === p ? "bg-sand" : "text-ink-soft hover:text-ink"}`}
                  >
                    {label}
                    {p === "review" && pending > 0 && <span className="ml-1.5 rounded-full bg-lime px-1.5 text-xs">{pending}</span>}
                  </a>
                ))}
              </nav>
              <a
                href="#/profile"
                className={`flex items-center gap-2 rounded-full py-1 pr-1 pl-1 text-sm transition sm:pl-3 ${page === "profile" ? "bg-sand" : "hover:bg-sand"}`}
                aria-label="Your profile"
              >
                <span className="hidden sm:inline">{me.name.split(" ")[0]}</span>
                <span className="grid size-7 place-items-center rounded-full bg-ink text-xs text-paper">{me.name[0].toUpperCase()}</span>
              </a>
            </>
          )}
        </header>

        <main className="py-6">
          {me === undefined && <p className="py-20 text-center text-muted">Loading…</p>}
          {me === null && <Auth onAuthed={setMe} />}
          {me && page === "upload" && (
            <Upload onUploaded={(found) => setReceipts((rs) => [...found, ...rs])} onReview={() => go("review")} pending={pending} />
          )}
          {me && page === "review" && <Review receipts={receipts} setReceipts={setReceipts} />}
          {me && page === "dashboard" && <Dashboard me={me} go={go} />}
          {me && page === "explore" && <Explore params={params} go={go} />}
          {me && page === "profile" && <Profile me={me} onSaved={setMe} onLogout={logout} />}
        </main>
      </div>
    </div>
  );
}
