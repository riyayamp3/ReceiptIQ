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
  const busy = queue.some((q) => q.state === "processing");

  async function handle(files) {
    const start = queue.length;
    setQueue((q) => [...q, ...[...files].map((f) => ({ name: f.name, state: "waiting" }))]);
    const set = (i, patch) => setQueue((q) => q.map((x, j) => (j === start + i ? { ...x, ...patch } : x)));
    // one at a time: OCR is CPU-bound on the server, parallel requests don't finish sooner
    for (const [i, f] of [...files].entries()) {
      set(i, { state: "processing" });
      const body = new FormData();
      body.append("file", f);
      try {
        const found = await api("/receipts", { method: "POST", body }); // a photo can hold several receipts
        onUploaded(found);
        set(i, { state: "done", msg: found.map((r) => r.merchant ?? "Unknown merchant").join(" + ") });
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

        <label
          onDragOver={(e) => (e.preventDefault(), setDragging(true))}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => (e.preventDefault(), setDragging(false), handle(e.dataTransfer.files))}
          className={`mx-auto mt-10 flex max-w-xl cursor-pointer flex-col items-center gap-2 rounded-2xl border border-dashed px-6 py-10 transition ${
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

