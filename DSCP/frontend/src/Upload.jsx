import { useState } from "react";
import { api } from "./api";

const STEPS = [
  ["Preprocess", "Grayscale, resize, denoise and contrast-boost the photo so text stands out."],
  ["Read", "Sarvam Vision reads printed or handwritten receipts and detects English, Hindi or Marathi on its own."],
  ["Extract", "Rules pull out merchant, date, items, GST and total from the raw text."],
  ["Verify", "You check and correct the result. Corrections become our evaluation data."],
];

export default function Upload({ onUploaded, onReview, pending }) {
  const [queue, setQueue] = useState([]);
  const [dragging, setDragging] = useState(false);
  const [tab, setTab] = useState("receipts"); // "receipts" | "passbook"
  const busy = queue.some((q) => q.state === "processing");

  async function handle(files) {
    const start = queue.length;
    setQueue((q) => [...q, ...[...files].map((f) => ({ name: f.name, state: "waiting" }))]);
    const set = (i, patch) => setQueue((q) => q.map((x, j) => (j === start + i ? { ...x, ...patch } : x)));
    for (const [i, f] of [...files].entries()) {
      set(i, { state: "processing" });
      const suffix = f.name.split(".").pop().toLowerCase();
      const isPdf = suffix === "pdf";
      const body = new FormData();
      body.append("file", f);
      const endpoint = isPdf ? "/passbook" : "/receipts";
      try {
        const found = await api(endpoint, { method: "POST", body });
        onUploaded(found);
        const label = isPdf
          ? `${found.length} transaction${found.length !== 1 ? "s" : ""} imported`
          : found.map((r) => r.merchant ?? "Unknown merchant").join(" + ");
        set(i, { state: "done", msg: label });
      } catch (e) {
        set(i, { state: "error", msg: e.message });
      }
    }
  }

  return (
    <div className="space-y-12">
      <section className="rounded-2xl bg-sand px-4 py-12 text-center sm:py-16">
        <p className="eyebrow justify-center">◈ Receipt intelligence</p>
        <h1 className="mx-auto mt-5 max-w-2xl text-3xl leading-tight font-normal tracking-tight sm:text-[2.6rem]">
          Turn every receipt into a clear picture of where your money goes.
        </h1>

        {/* Tab switcher */}
        <div className="mt-8 flex justify-center">
          <div className="flex rounded-full bg-paper p-1 text-sm shadow-inner">
            {[["receipts", "Receipt photos"], ["passbook", "Bank passbook PDF"]].map(([t, label]) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`rounded-full px-4 py-1.5 transition ${tab === t ? "bg-ink text-paper" : "text-muted hover:text-ink"}`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        {tab === "receipts" && (
          <label
            onDragOver={(e) => (e.preventDefault(), setDragging(true))}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => (e.preventDefault(), setDragging(false), handle(e.dataTransfer.files))}
            className={`mx-auto mt-8 flex max-w-xl cursor-pointer flex-col items-center gap-2 rounded-2xl border border-dashed px-6 py-10 transition ${
              dragging ? "border-ink bg-paper" : "border-ink/30 hover:bg-paper/60"
            }`}
          >
            <span className="btn">Choose receipt photos</span>
            <span className="text-sm text-muted">or drop JPG / PNG files here · printed or handwritten · English, हिंदी, मराठी</span>
            <input
              type="file"
              accept="image/*"
              multiple
              className="sr-only"
              onChange={(e) => (handle(e.target.files), (e.target.value = ""))}
            />
          </label>
        )}

        {tab === "passbook" && (
          <div className="mx-auto mt-8 max-w-xl space-y-4">
            <label
              onDragOver={(e) => (e.preventDefault(), setDragging(true))}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => (e.preventDefault(), setDragging(false), handle(e.dataTransfer.files))}
              className={`flex cursor-pointer flex-col items-center gap-2 rounded-2xl border border-dashed px-6 py-10 transition ${
                dragging ? "border-ink bg-paper" : "border-ink/30 hover:bg-paper/60"
              }`}
            >
              <svg viewBox="0 0 24 24" className="size-10 text-muted" fill="none" stroke="currentColor" strokeWidth="1.2">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <polyline points="14 2 14 8 20 8" />
                <line x1="9" y1="13" x2="15" y2="13" />
                <line x1="9" y1="17" x2="13" y2="17" />
              </svg>
              <span className="btn">Choose passbook PDF</span>
              <span className="text-sm text-muted">SBI · HDFC · ICICI · Axis · Kotak · IDFC and more</span>
              <input
                type="file"
                accept=".pdf"
                multiple
                className="sr-only"
                onChange={(e) => (handle(e.target.files), (e.target.value = ""))}
              />
            </label>
            <div className="rounded-xl bg-paper p-4 text-left text-sm text-muted space-y-1.5">
              <p className="font-medium text-ink">How passbook import works</p>
              <p>Export a PDF statement from your bank's net-banking portal and upload it here.</p>
              <p>Each transaction is imported as a pending receipt. Open the Review tab to verify and correct merchants, categories and amounts before confirming.</p>
              <p className="pt-1">📌 Debit transactions are treated as expenses. Credits (salary, refunds) are imported too — delete any you don't need.</p>
            </div>
          </div>
        )}

        {queue.length > 0 && (
          <ul className="mx-auto mt-6 max-w-xl space-y-2 text-left text-sm">
            {queue.map((q, i) => (
              <li key={i} className="flex items-center justify-between gap-3 rounded-xl bg-paper px-4 py-3">
                <span className="truncate">{q.name}</span>
                <span className={`shrink-0 text-xs ${q.state === "error" ? "text-warn" : "text-muted"}`}>
                  {{ waiting: "Waiting…", processing: "Reading…", done: "✓ " + q.msg, error: "✕ " + q.msg }[q.state]}
                </span>
              </li>
            ))}
          </ul>
        )}

        {pending > 0 && !busy && (
          <button className="btn mt-6" onClick={onReview}>
            Review {pending} receipt{pending > 1 && "s"} →
          </button>
        )}
      </section>

      <section>
        <p className="eyebrow">◈ How it works</p>
        <h2 className="mt-2 text-3xl font-normal tracking-tight">From photo to data in four steps</h2>
        <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {STEPS.map(([title, text], i) => (
            <div key={title} className="rounded-2xl bg-sand p-5">
              <span className="rounded-full bg-lime px-2.5 py-0.5 text-xs">Step {i + 1}</span>
              <h3 className="mt-4 text-lg">{title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-muted">{text}</p>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
