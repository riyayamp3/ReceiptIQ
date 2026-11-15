import { useEffect, useRef, useState } from "react";
import { api, inr } from "./api";
import { MemberFilter } from "./MemberFilter";

const PROVIDER_LABEL = { sarvam: "Sarvam", groq: "Groq" };

export default function Chat({ me }) {
  const [roster, setRoster] = useState([]);
  const [member, setMember] = useState(me?.id ?? null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [availableProviders, setAvailableProviders] = useState([]);
  const [provider, setProvider] = useState(null);
  const bottomRef = useRef(null);

  useEffect(() => {
    api("/groups/roster").then(setRoster).catch(() => setRoster(me ? [me] : []));
    api("/providers").then((ps) => {
      setAvailableProviders(ps);
      setProvider(ps[0] ?? null);
    }).catch(() => {});
  }, [me]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function send(e) {
    e.preventDefault();
    const text = input.trim();
    if (!text || loading) return;
    const userMsg = { role: "user", content: text };
    setInput("");
    setMessages((m) => [...m, userMsg]);
    setLoading(true);
    try {
      const body = { message: text, history: messages.slice(-10), member_id: member || null };
      if (availableProviders.length > 1 && provider) body.provider = provider;
      const res = await api("/chat", { method: "POST", json: body });
      setMessages((m) => [
        ...m,
        { role: "assistant", content: res.answer, provider_used: res.provider_used, receipts: res.receipts },
      ]);
    } catch (err) {
      setMessages((m) => [...m, { role: "assistant", content: `✕ ${err.message}`, error: true }]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <p className="eyebrow">◈ Spending assistant</p>
        <h2 className="mt-2 text-3xl font-normal tracking-tight">Ask about your spending</h2>
        <p className="mt-1 text-sm text-muted">Answers come from your actual receipts — no guesses.</p>
      </div>

      {/* Controls row */}
      <div className="flex flex-wrap items-center gap-3 text-sm">
        {roster.length > 1 && (
          <label className="flex items-center gap-2">
            <span className="text-muted">Member</span>
            <MemberFilter roster={roster} value={member} onChange={setMember} me={me} />
          </label>
        )}
        {availableProviders.length > 1 && (
          <label className="flex items-center gap-2">
            <span className="text-muted">Provider</span>
            <select
              className="field"
              value={provider ?? ""}
              onChange={(e) => setProvider(e.target.value)}
            >
              {availableProviders.map((p) => (
                <option key={p} value={p}>{PROVIDER_LABEL[p] ?? p}</option>
              ))}
            </select>
          </label>
        )}
      </div>

      {/* Conversation */}
      <div className="flex flex-col gap-3 rounded-2xl bg-sand px-4 py-4 min-h-[320px] max-h-[520px] overflow-y-auto">
        {messages.length === 0 && (
          <div className="m-auto space-y-2 text-center text-sm text-muted">
            <p className="text-base">What do you want to know?</p>
            {[
              "How much did I spend this month?",
              "What was my biggest expense?",
              "Show me food bills for October",
              "How much did Mum spend on groceries?",
            ].map((hint) => (
              <button
                key={hint}
                className="mx-1 rounded-full border border-line bg-paper px-3 py-1 text-ink hover:bg-white"
                onClick={() => setInput(hint)}
              >
                {hint}
              </button>
            ))}
          </div>
        )}
        {messages.map((msg, i) => (
          <Bubble key={i} msg={msg} me={me} />
        ))}
        {loading && (
          <div className="self-start rounded-2xl rounded-tl-sm bg-paper px-4 py-3 text-sm text-muted">
            Thinking…
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <form onSubmit={send} className="flex gap-2">
        <input
          className="field flex-1"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about your spending…"
          disabled={loading}
        />
        <button className="btn" disabled={loading || !input.trim()}>Send</button>
      </form>
    </div>
  );
}

function Bubble({ msg, me }) {
  const isUser = msg.role === "user";
  return (
    <div className={`flex flex-col gap-1 ${isUser ? "items-end" : "items-start"}`}>
      <div
        className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap
          ${isUser ? "rounded-tr-sm bg-ink text-paper" : msg.error ? "rounded-tl-sm bg-[#fce8e8] text-warn" : "rounded-tl-sm bg-paper"}`}
      >
        {msg.content}
      </div>
      {/* Cited receipts */}
      {msg.receipts?.length > 0 && (
        <div className="max-w-[85%] space-y-1">
          {msg.receipts.slice(0, 5).map((r) => (
            <div key={r.id} className="rounded-xl bg-paper px-3 py-1.5 text-xs text-muted">
              {r.merchant ?? "Unknown"} · {r.date} · {inr(r.total)}
            </div>
          ))}
        </div>
      )}
      {/* Provider badge */}
      {msg.provider_used && (
        <span className="text-xs text-muted">{PROVIDER_LABEL[msg.provider_used] ?? msg.provider_used}</span>
      )}
    </div>
  );
}
