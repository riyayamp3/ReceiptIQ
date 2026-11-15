# Design Document — Family Accounts & RAG Chatbot

Feature: `rag-chatbot-family-accounts`
Spec type: feature · Workflow: fast-task

## 1. Overview

This design adds two capabilities to ReceiptIQ, grounded in the existing FastAPI + SQLite + React codebase:

1. **Family accounts** — separate member logins share one Family Group. Receipts stay attributed to the member who made the purchase (Member Label) but are visible family-wide. Dashboard, Explore, and Chat can be filtered by member.
2. **RAG chatbot** — a hybrid assistant that answers spending questions by combining **tool calling** (exact figures via the existing `budget_status`, `insights`, and `analysis` functions) with **retrieval-augmented generation** (fuzzy, item-level search over receipt embeddings stored in `receiptiq.db` via sqlite-vec).

Key decisions carried from clarification:
- **LLM**: Sarvam (already integrated for OCR) preferred; Groq as fallback. No OpenAI.
- **Vector DB**: sqlite-vec inside `receiptiq.db` (single-file SQLite philosophy).
- **Embeddings**: Sarvam **EmbeddingGemma 2** (768-dim, normalized; dot product = cosine similarity) over the same `SARVAM_API_KEY` already used for OCR. Sarvam already receives receipt content during OCR extraction, so generating embeddings from the resulting text adds no new data exposure. The only thing that leaves the machine at query time is the chat request to the cloud LLM.
- **Family accounts**: one family group per user; all data scoped by `family_group_id`, not `user_id`.
- **Member attribution**: tag receipts with the member at review time; default to the confirming member.

## 2. Architecture

```
┌─ Frontend (React/Vite) ─────────────────────────────────────────┐
│  App.jsx (hash router)  +tabs: upload·review·dashboard·explore·chat │
│    Chat.jsx (new)         member filter + conversation + input     │
│    Review.jsx (+member label)  Dashboard/Explore.jsx (+member filter)│
│    api.js (fetch wrapper, 401 handling)                          │
└───────────────────────────────┬─────────────────────────────────┘
                                 │ /api/* (httpOnly session cookie)
┌─ Backend (FastAPI) ────────────▼─────────────────────────────────┐
│  auth.py        current_user dependency, /api/auth/*, /api/me    │
│  family.py(new) /api/family/*  create/join/roster                  │
│  app.py         /api/receipts · /api/stats · /api/analysis ·       │
│                 /api/images  — ALL re-scoped by family_group_id    │
│                 + /api/chat (new) + embedding hooks on confirm/edit/delete │
│  chat.py(new)   LLM provider (Sarvam|Groq) + chat loop + tools    │
│  embeddings.py(new) Sarvam EmbeddingGemma 2  text→768-dim vector    │
│  vectorstore.py(new) sqlite-vec virtual table: upsert/delete/retrieve │
│  insights.py / analysis.py  unchanged pure functions (re-used as tools) │
│  sarvam.py      existing OCR (unchanged)                          │
│  db.py          schema + migrate() + sqlite-vec load in connect()  │
└───────────────────────────────┬─────────────────────────────────┘
                                 │
        receiptiq.db (single SQLite file)
          users · sessions · images · receipts · items
          family_groups (new) · receipt_embeddings (vec0, new)
```

**Data flow for a chat question:**
1. `Chat.jsx` POSTs `{message, member_id?, provider?}` to `/api/chat` with the session cookie. When both provider keys are configured the UI shows a provider dropdown; the selected value is sent as `provider` (omitted when only one provider is available).
2. `auth.current_user` authenticates and yields the requesting user (and thus `family_group_id`).
3. `chat.answer()` builds a system prompt containing the roster and current scope, then loops: call the LLM with tool definitions; for each tool call, execute it **scoped to the requesting family group** (member filter resolved from the question/UI); feed results back; repeat until the LLM returns a final answer.
4. Exact-figure questions route to tool calls (`get_budget`, `get_spending`, `get_insights`); fuzzy/item questions route to `search_receipts` (RAG over `receipt_embeddings`, partition-scoped by `family_group_id`).
5. A grounding guard refuses when no tool or RAG returned data.

## 3. Data Model

All additions follow the existing `SCHEMA` + `ADDED_COLUMNS` migration pattern in `db.py`. Nothing is discarded.

### 3.1 New table: `family_groups`

```sql
create table if not exists family_groups (
  id integer primary key,
  name text not null,
  join_code text not null unique,          -- short random code others use to join
  created_by integer references users(id) on delete set null,
  created_at text default current_timestamp
);
```

### 3.2 Added columns

A Member is a User belonging to a Family Group (`users.family_group_id`). The Member Label is `receipts.member_id`, the user who made the purchase (may differ from the uploader `receipts.user_id`).

```python
ADDED_COLUMNS = {
    "receipts": {  # existing entries unchanged; appended below
        "engine": "text", "lang": "text", "field_confidence": "text",
        "cgst": "real", "sgst": "real", "igst": "real", "gst_rate": "real",
        "gstin": "text", "service_type": "text", "user_id": "integer",
        # NEW for this feature:
        "family_group_id": "integer",
        "member_id": "integer",
    },
    "images": {"user_id": "integer"},
    "users": {"family_group_id": "integer"},  # NEW
}
```

### 3.3 New virtual table: `receipt_embeddings` (sqlite-vec)

sqlite-vec `vec0` with `family_group_id` as a **partition key** — the index is sharded per family group, so scoped KNN retrieval is fast and cannot leak across families. `receipt_id` is the integer primary key (= rowid). Embeddings are normalized 768-dim vectors from EmbeddingGemma 2.

```sql
create virtual table if not exists receipt_embeddings using vec0(
  receipt_id     integer primary key,
  family_group_id integer partition key,   -- shards the index by family group
  embedding      float[768]                -- EmbeddingGemma 2, normalized
);
```

### 3.4 Migration (`migrate()`)

After creating tables and adding columns, backfill existing data so the app keeps working against a pre-existing `receiptiq.db`:

```python
def migrate():
    import sqlite_vec
    with closing(connect()) as con, con:
        con.executescript(SCHEMA)
        for table, columns in ADDED_COLUMNS.items():           # existing pattern
            have = {r["name"] for r in con.execute(f"pragma table_info({table})")}
            for col, kind in columns.items():
                if col not in have:
                    con.execute(f"alter table {table} add column {col} {kind}")
        # NEW: backfill family structures onto existing data
        # each pre-existing user without a group becomes a one-member family
        for u in con.execute("select id, name from users where family_group_id is null"):
            code = secrets.token_urlsafe(4)
            fgid = con.execute(
                "insert into family_groups (name, join_code, created_by) values (?, ?, ?)",
                (f"{u['name']}'s group", code, u["id"]),
            ).lastrowid
            con.execute("update users set family_group_id = ? where id = ?", (fgid, u["id"]))
        # existing receipts inherit their uploader's group; confirmed receipts default the
        # uploader as the member label (the only member known at migration time)
        con.execute("""update receipts set family_group_id = (
                         select family_group_id from users where users.id = receipts.user_id)
                       where family_group_id is null""")
        con.execute("""update receipts set member_id = user_id
                       where status = 'confirmed' and member_id is null""")
        # existing image-migration step (unchanged) ...
    # create the vec0 table (extension already loaded in connect())
    with closing(connect()) as con:
        con.execute("""create virtual table if not exists receipt_embeddings using vec0(
                         receipt_id integer primary key,
                         family_group_id integer partition key,
                         embedding float[768])""")
```

A user with no family group after signup is a member of nothing until they create or join one; receipts they upload are still stored (with their `family_group_id` once assigned, or null and backfilled lazily). In practice signup creates the account (Req 1.1); family membership is a separate, explicit step (create/join).

### 3.5 Loading sqlite-vec

`connect()` loads the `vec0` extension on every connection (the codebase opens a fresh connection per request):

```python
import sqlite_vec
def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("pragma foreign_keys = on")
    sqlite_vec.load(con)   # enables load_extension + loads vec0
    return con
```

`sqlite-vec` is added to `backend/requirements.txt`. Loading is cheap; for heavier load a connection pool would amortize it (out of scope here).

## 4. Backend Components

### 4.1 `family.py` (new) — Family Group endpoints

An `APIRouter(prefix="/api/family")`. Re-uses `auth.current_user`.

```python
class FamilyCreate(BaseModel):
    name: str

class FamilyJoin(BaseModel):
    join_code: str

@router.post("")
def create_family(body: FamilyCreate, user=Depends(current_user)):
    if user["family_group_id"] is not None:
        raise HTTPException(409, "You are already in a family group")
    code = secrets.token_urlsafe(4)
    with closing(connect()) as con, con:
        fgid = con.execute("insert into family_groups (name, join_code, created_by) values (?,?,?)",
                            (body.name.strip() or f"{user['name']}'s family", code, user["id"])).lastrowid
        con.execute("update users set family_group_id = ? where id = ?", (fgid, user["id"]))
        return family_info(con, fgid)

@router.post("/join")
def join_family(body: FamilyJoin, user=Depends(current_user)):
    with closing(connect()) as con, con:
        fg = con.execute("select * from family_groups where join_code = ?", (body.join_code.strip(),)).fetchone()
        if not fg: raise HTTPException(404, "No family group with that code")
        if user["family_group_id"] == fg["id"]: raise HTTPException(409, "Already a member")
        if user["family_group_id"] is not None: raise HTTPException(409, "You are already in a family group")
        con.execute("update users set family_group_id = ? where id = ?", (fg["id"], user["id"]))
        return family_info(con, fg["id"])

@router.get("/roster")
def roster(user=Depends(current_user)):
    if not user["family_group_id"]: raise HTTPException(404, "No family group")
    with closing(connect()) as con:
        return [dict(r) for r in con.execute(
            "select id, name, email from users where family_group_id = ? order by id", (user["family_group_id"],))]
```

`family_info` returns `{id, name, join_code, members}`. A user belongs to exactly one family group; create/join when already in a group is rejected (Req 1.2/1.3). The roster is the set of users sharing `family_group_id` (Req 1.5).

### 4.2 `embeddings.py` (new) — Sarvam EmbeddingGemma 2

```python
import os
EMBED_DIM = 768
MODEL = "embeddinggemma-2"   # /v2/embeddings, same SARVAM_API_KEY as OCR

def available():
    return bool(os.getenv("SARVAM_API_KEY"))

def receipt_text(r, items):
    """The text we embed: merchant, date, category, items, and raw OCR text (Req 6.2)."""
    names = ", ".join(i["name"] for i in items if i.get("name"))
    return " | ".join(filter(None, [r.get("merchant"), r.get("date"), r.get("category"),
                                     f"items: {names}" if names else None, r.get("raw_text")]))

def embed(texts):
    """texts -> list of 768-dim normalized vectors via Sarvam EmbeddingGemma 2."""
    from sarvamai import SarvamAI
    c = SarvamAI(api_subscription_key=os.environ["SARVAM_API_KEY"])
    out = []
    for t in texts:
        res = c.embeddings(model=MODEL, input=t)   # SDK call to /v2/embeddings
        out.append(res.embeddings[0] if hasattr(res, "embeddings") else res["data"][0]["embedding"])
    return out
```

`embed` is the single seam to the cloud embedding service, so tests substitute it with a deterministic mock (e.g., a hash-based pseudo-vector) to exercise the vector store without network calls.

### 4.3 `vectorstore.py` (new) — sqlite-vec operations

```python
from db import connect, EMBED_DIM  # EMBED_DIM = 768

def upsert(receipt_id, family_group_id, vector):
    """Insert or replace the embedding for a receipt (used on confirm + edit, Req 6.2/6.3)."""
    with closing(connect()) as con, con:
        con.execute("delete from receipt_embeddings where receipt_id = ?", (receipt_id,))
        con.execute("insert into receipt_embeddings (receipt_id, family_group_id, embedding) values (?,?,?)",
                    (receipt_id, family_group_id, _vec(vector)))

def remove(receipt_id):
    with closing(connect()) as con, con:
        con.execute("delete from receipt_embeddings where receipt_id = ?", (receipt_id,))   # Req 6.4

def retrieve(query_vec, family_group_id, k=8):
    """Family-scoped KNN. Partition key constrains the search to this family (Req 5.2/6.5)."""
    with closing(connect()) as con:
        rows = con.execute("""select receipt_id, distance from receipt_embeddings
                              where embedding match ? and k = ? and family_group_id = ?
                              order by distance""", (_vec(query_vec), k, family_group_id)).fetchall()
    return [r["receipt_id"] for r in rows]
```

`_vec` serializes a list to the JSON/binary form sqlite-vec accepts. The `family_group_id = ?` partition-key equality constraint guarantees no receipt from another family is ever returned, regardless of similarity.

### 4.4 `chat.py` (new) — LLM provider, tools, chat loop

**Provider selection with runtime switching and automatic fallback** (Req 7.1–7.7):

```python
def providers(preferred: str | None = None) -> list[str]:
    """Return an ordered list of available providers.
    preferred (if given and available) is placed first; the other follows as fallback.
    Raises 503 when no provider key is configured."""
    available = []
    if os.getenv("SARVAM_API_KEY"): available.append("sarvam")
    if os.getenv("GROQ_API_KEY"):   available.append("groq")
    if not available:
        raise HTTPException(503, "No LLM provider is configured. Set SARVAM_API_KEY or GROQ_API_KEY.")
    if preferred and preferred in available:
        # move the preferred provider to the front; the other becomes the fallback
        available = [preferred] + [p for p in available if p != preferred]
    return available
```

`answer()` receives a `preferred_provider` parameter (from the request body, `None` when only one provider is configured). The chat loop iterates the ordered provider list, catching API errors to trigger fallback:

```python
def answer(user, message, history, member_filter, preferred_provider=None):
    provider_list = providers(preferred_provider)   # [preferred, fallback] or [only]
    last_exc = None
    for prov in provider_list:
        try:
            return _answer_with(prov, user, message, history, member_filter)
        except (APIError, RateLimitError, TimeoutError) as exc:
            last_exc = exc          # log and try the next provider
            continue
    # all providers failed
    raise HTTPException(502, f"All LLM providers failed. Last error: {last_exc}")
```

`_answer_with(prov, ...)` contains the existing chat loop logic (scope resolution, tool calls, grounding guard) but uses the specified provider's SDK. Sarvam chat (Token Factory, OpenAI-compatible tool calling via `sarvamai`) and Groq chat (`groq`) are each wrapped behind a thin adapter; both expose the same `chat(messages, tools)` → response interface.

**Provider selector in `Chat.jsx`**: when both keys are configured the backend exposes which providers are available via the `/api/chat` response (or a lightweight `/api/providers` probe on mount). `Chat.jsx` renders a provider dropdown only when more than one provider is available:

```jsx
// shown only when availableProviders.length > 1
<select value={provider} onChange={e => setProvider(e.target.value)}>
  {availableProviders.map(p => <option key={p} value={p}>{p}</option>)}
</select>
```

The selected provider is included in the POST body as `provider` (optional field). When only one provider is configured the dropdown is not rendered and the field is omitted.

**Tools** — thin wrappers over the existing pure functions, always scoped server-side to the requesting family group. The LLM never controls `family_group_id`; it may only choose a `member` (by name, `"me"`, or omit for the whole family), validated against the roster.

```python
def scope(user, member_ref, roster):
    """Resolve the (family_group_id, member_id) scope. family_group_id is always the requester's."""
    fgid = user["family_group_id"]
    if member_ref in (None, "", "all", "family", "everyone"):
        return fgid, None                      # whole family (Req 4.4)
    if member_ref in ("me", "my", "mine", user["name"]):
        return fgid, user["id"]               # asking member (Req 4.3)
    m = next((m for m in roster if m["name"].lower() == member_ref.lower()), None)
    if not m:
        raise ValueError("Unknown member")    # named member not in family -> reject (Req 4.2)
    return fgid, m["id"]

def tool_get_budget(con, fgid, member_id, budget):
    rows = scoped_receipts(con, fgid, member_id)
    return budget_status(rows, budget, date.today())            # Req 4.5

def tool_get_spending(con, fgid, member_id, category=None, merchant=None, month=None):
    return analysis(con, fgid, category, merchant, month, member_filter=member_id)

def tool_search(query_vec, fgid):
    ids = vectorstore.retrieve(query_vec, fgid)
    return load_receipts(ids)                 # full receipts for the LLM to cite (Req 5.3/11.1)
```

`scoped_receipts` and `analysis` are extended to accept `family_group_id` + optional `member_id` instead of `user_id` (see §4.5).

**Chat loop** with a grounding guard (Req 4.6, 11.2):

```python
def _answer_with(prov, user, message, history, member_filter):
    """Single-provider chat loop. Called by answer(); raises on provider errors."""
    if not user["family_group_id"]:
        return chat_response("Join or create a family group first.", user, tools=[], receipts=[])
    fgid = user["family_group_id"]
    roster = family_roster(fgid)
    sys = system_prompt(user, roster)         # instructs: answer only from tool/RAG data; cite receipts; refuse if none
    messages = [sys, *history, {"role": "user", "content": message}]
    used_data, receipts_used = False, []
    for _ in range(MAX_ROUNDS):
        resp = llm_chat(prov, messages, tools=TOOL_SCHEMAS)
        if not resp.tool_calls:
            return chat_response(resp.content if used_data else REFUSAL, user, receipts_used)
        for tc in resp.tool_calls:
            result = run_tool(tc, user, roster, member_filter)   # always scoped to fgid
            if has_data(result):
                used_data = True
                receipts_used += result.get("receipts", [])
            messages.append(tool_result(tc, result))
    return chat_response(REFUSAL, user, receipts_used)   # exhausted rounds without a final answer
```

`REFUSAL` = "I don't have enough information in your receipts to answer that." The guard ensures no answer with figures is produced when no tool/RAG returned data (Req 4.6/11.2). The conversation history is held client-side per browser session (Req 9.2); the backend is stateless and always re-derives `family_group_id` from auth.

### 4.5 `app.py` changes — re-scoping by family group

Every query currently scoped `where user_id = ?` is re-scoped to the requesting member's family group, with an optional member filter. The `current_user` dependency already gives us `user["family_group_id"]`.

```python
def fg_scope(user, member=None):                 # member is a user id or None
    q = "where family_group_id = ? and status = 'confirmed'"
    args = [user["family_group_id"]]
    if member:
        q += " and member_id = ?"; args.append(member)
    return q, args

def get_receipt(con, rid, user):                # family-wide visibility (Req 3.1/3.4)
    row = con.execute("select * from receipts where id = ? and family_group_id = ?",
                      (rid, user["family_group_id"])).fetchone()
    if not row: raise HTTPException(404, "Receipt not found")
    ...

@app.get("/api/receipts")
def list_receipts(member: int | None = None, user=User):
    with closing(connect()) as con:
        q, args = "select id from receipts where family_group_id = ?", [user["family_group_id"]]
        if member: q += " and member_id = ?"; args.append(member)
        return [get_receipt(con, r["id"], user) for r in con.execute(q + " order by date desc, id desc", args)]

@app.get("/api/stats")
def stats(member: int | None = None, user=User):
    with closing(connect()) as con:
        where, args = fg_scope(user, member)
        ...   # budget_status / insights / compare_months over the scoped receipts
```

`/api/analysis` gains a `member` query param forwarded to `analysis()`. `insights.py` and `analysis.py` keep their pure-function signatures; their callers pass the family/member-scoped receipt set or the `family_group_id` + `member_filter`.

**Embedding hooks** (Req 6.2–6.4): on confirm/edit in `update_receipt` (when `status == "confirmed"`), upsert the embedding; on `delete_receipt`, remove it.

```python
@app.put("/api/receipts/{rid}")
def update_receipt(rid, body: ReceiptIn, member: int | None = None, user=User):
    ...
    if body.status == "confirmed" and embeddings.available():
        label = member or user["id"]            # default to confirming member (Req 2.3)
        con.execute("update receipts set member_id = ? where id = ?", (label, rid))
        r = get_receipt(con, rid, user)
        vec = embed([receipt_text(r, r["items"])])[0]
        vectorstore.upsert(rid, user["family_group_id"], vec)   # Req 6.2/6.3
    return get_receipt(con, rid, user)

@app.delete("/api/receipts/{rid}")
def delete_receipt(rid, user=User):
    with closing(connect()) as con, con:
        get_receipt(con, rid, user)
        vectorstore.remove(rid)                 # Req 6.4 (before the row goes)
        con.execute("delete from receipts where id = ?", (rid,))
        ...
```

### 4.6 `db.py` — public `EMBED_DIM`; schema constants

`EMBED_DIM = 768` is exported so `vectorstore` and the `vec0` creation agree. The vec0 dimension is fixed at table creation; changing the embedding model requires dropping/recreating `receipt_embeddings` and re-indexing confirmed receipts.

## 5. Frontend Components

### 5.1 `App.jsx` — add the Chat tab

```jsx
const TABS = [["upload","Upload"],["review","Review"],["dashboard","Dashboard"],
              ["explore","Explore"],["chat","Chat"]];   // new entry
const PAGES = new Set([...TABS.map(([p])=>p), "profile"]);
// ...
{me && page === "chat" && <Chat me={me} />}
```

### 5.2 `Chat.jsx` (new) — chat UI + member filter + provider selector

```jsx
export default function Chat({ me }) {
  const [roster, setRoster] = useState([]);
  const [member, setMember] = useState(me.id);     // default to logged-in member (Req 10.2)
  const [messages, setMessages] = useState([]);    // conversation for this browser session (Req 9.2)
  const [input, setInput] = useState("");
  const [availableProviders, setAvailableProviders] = useState([]);
  const [provider, setProvider] = useState(null);  // null = let backend pick default
  useEffect(() => { api("/family/roster").then(setRoster).catch(() => setRoster([me])); }, [me]);
  useEffect(() => { api("/providers").then(ps => { setAvailableProviders(ps); setProvider(ps[0] ?? null); }).catch(()=>{}); }, []);

  async function send(e) {
    e.preventDefault();
    const text = input.trim(); if (!text) return;
    setInput(""); setMessages((m) => [...m, {role:"user", content:text}]);
    const body = { message: text, member_id: Number(member) || null };
    if (availableProviders.length > 1 && provider) body.provider = provider;
    const res = await api("/chat", { method:"POST", json: body }); // Req 9.3
    setMessages((m) => [...m, {role:"assistant", content:res.answer, provider_used:res.provider_used}]);
  }
  return (
    <div>
      {/* Provider selector — rendered only when both keys are configured (Req 7.2/7.3) */}
      {availableProviders.length > 1 && (
        <select value={provider ?? ""} onChange={e => setProvider(e.target.value)}>
          {availableProviders.map(p => <option key={p} value={p}>{p}</option>)}
        </select>
      )}
      <MemberFilter roster={roster} value={member} onChange={setMember} me={me} /> {/* Req 10.1 */}
      <Conversation messages={messages} />
      <form onSubmit={send}><input className="field" value={input} onChange={(e)=>setInput(e.target.value)} placeholder="Ask about your spending…" /><button className="btn">Send</button></form>
    </div>
  );
}
```

### 5.3 `Review.jsx` — Member Label dropdown

Each `ReceiptCard` gains a member select populated from the roster, defaulting to the logged-in member. On confirm it sends `member` in the PUT body.

```jsx
const [roster, setRoster] = useState([]);
useEffect(() => { api("/family/roster").then(setRoster).catch(() => {}); }, []);
// inside ReceiptCard:
<Field label="Spent by">
  <select className="field" value={r.member ?? me.id} onChange={set("member")}>   {/* Req 2.1 */}
    {roster.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
  </select>
</Field>
// save() adds member: num(r.member) ?? me.id  to the PUT body (default = confirmer, Req 2.3)
```

### 5.4 `MemberFilter.jsx` (shared) — Dashboard / Explore / Chat

A small dropdown built from the roster with an explicit "All family" option; clearing the selection shows the whole family (Req 10.3/10.4). Dashboard and Explore pass the selected `member` to `/api/stats` and `/api/analysis` and refetch on change.

```jsx
export function MemberFilter({ roster, value, onChange, me }) {
  return (
    <select className="field" value={value ?? ""} onChange={(e)=>onChange(e.target.value ? Number(e.target.value) : null)}>
      <option value="">{roster.length > 1 ? "All family" : "Everyone"}</option>
      {roster.map((m) => <option key={m.id} value={m.id} selected={m.id===me.id}>{m.name}{m.id===me.id?" (you)":""}</option>)}
    </select>
  );
}
```

### 5.5 `api.js`

Unchanged wrapper; `api("/chat", {method:"POST", json:{...}})` works as-is. 401 handling already dispatches `logged-out`.

## 6. API Interfaces

| Method | Path | Auth | Body / Params | Returns |
|---|---|---|---|---|
| POST | `/api/family` | session | `{name}` | `{id, name, join_code, members}` |
| POST | `/api/family/join` | session | `{join_code}` | family info |
| GET  | `/api/family/roster` | session | — | `[{id, name, email}]` |
| GET  | `/api/receipts` | session | `?member=<uid>` | receipts (family-wide) |
| PUT  | `/api/receipts/{rid}` | session | `ReceiptIn` + `?member=` | receipt (sets member label on confirm) |
| GET  | `/api/stats` | session | `?member=<uid>` | stats (scoped) |
| GET  | `/api/analysis` | session | `?category&merchant&month&member` | analysis (scoped) |
| GET  | `/api/providers` | session | — | `["sarvam","groq"]` (the configured providers, in preference order) |
| POST | `/api/chat` | session | `{message, member_id?, provider?}` | `{answer, member:{id,name}, family_group_id, receipts?[], provider_used}` |

Chat response always carries the member identity and family scope (Req 8.4). Unauthenticated chat → 401 (Req 8.3).

## 7. Error Handling

- **No family group**: `/api/chat` returns a guidance message ("Join or create a family group first."); roster/scoped endpoints return 404 or empty for familyless users.
- **No LLM provider**: `providers()` raises 503 with a clear message (Req 7.7); `/api/chat` surfaces it.
- **Provider failure with fallback available**: when the selected/primary provider raises an API error, rate-limit error, or timeout, `answer()` automatically retries with the other configured provider. The response includes a `provider_used` field so the caller knows which provider actually answered (Req 7.5).
- **Both providers fail**: `answer()` raises 502 with a combined error message describing both failures (Req 7.6).
- **No embeddings provider** (no `SARVAM_API_KEY`): embeddings are skipped; RAG (`search_receipts`) returns no results and the chatbot falls back to tool-calling only. Tool-calling still works if a chat LLM key is configured.
- **Unknown member reference** (LLM names a member not in the roster): rejected server-side; the tool falls back to the default scope and the LLM is told the name was invalid.
- **Grounding failure**: no tool/RAG data → refusal message (Req 11.2); the guard overrides any LLM attempt to fabricate.
- **Embedding/LLM API errors**: wrapped like the existing Sarvam OCR error handling (`ApiError` → readable message, 502 upstream / 400 client); chat returns a 502 with the provider message rather than crashing.
- **Migration**: schema additions are additive and guarded by `ADDED_COLUMNS`; backfills are idempotent and never delete rows (Req 1.6).

## 8. Correctness Properties

*A property is a characteristic that should hold across all valid executions. Each below is universally quantified and implementable as an automated test (property-based, ≥100 iterations where the LLM/embedding service is mocked for determinism).*

### Property 1: Signup round-trip

For any valid signup request (non-empty name, valid email, password ≥ 8 chars, unique email), ReceiptIQ SHALL create an individual account that persists and is retrievable, and SHALL reject a duplicate email.

**Validates: Requirements 1.1**

### Property 2: Roster completeness & membership

For any family group, the roster SHALL equal exactly the set of users carrying that `family_group_id`. Creating a family group SHALL make the creator the sole first member, and any non-member joining SHALL add exactly that user, increasing the roster by one and containing the joiner — all persisted across fresh connections.

**Validates: Requirements 1.2, 1.3, 1.4, 1.5**

### Property 3: Migration preserves data and backfills family structures

For any pre-existing `receiptiq.db` containing users, receipts, and items, running `migrate()` SHALL leave every existing row intact (counts unchanged), SHALL give every user a `family_group_id`, and SHALL give every confirmed receipt a `member_id`. Empty and pending-only databases SHALL migrate without error.

**Validates: Requirements 1.6**

### Property 4: Member attribution invariant

For any receipt, confirming it SHALL store a Member Label. If a member is explicitly selected, the stored `member_id` SHALL equal the selection; if none is selected, it SHALL equal the confirming member's id. Every confirmed receipt SHALL carry a non-null `member_id` that references a user in the receipt's family group.

**Validates: Requirements 2.2, 2.3, 2.4**

### Property 5: Family isolation — no cross-family leakage

For any two members of different family groups, a member of one SHALL never receive, via `/api/receipts`, `/api/stats`, `/api/analysis`, `/api/images`, or any chat tool, any receipt or aggregated figure derived from the other family's receipts, regardless of how the request is parameterized.

**Validates: Requirements 3.1, 3.4, 3.5, 8.2**

### Property 6: Member filter correctness

For any family group and any member filter applied to `/api/receipts`, `/api/stats`, or `/api/analysis`, every returned receipt/row SHALL carry that member's label and the set SHALL be complete (all of that member's receipts in scope). With no member filter, the result SHALL include all receipts in the family group.

**Validates: Requirements 3.2, 3.3, 10.3, 10.4**

### Property 7: Embedding lifecycle

For any receipt, confirming it SHALL create an embedding row in `receipt_embeddings`; editing a confirmed receipt SHALL regenerate that row; deleting the receipt SHALL remove the row. No embedding row SHALL survive its receipt. (The embed service is mocked for determinism.)

**Validates: Requirements 6.2, 6.3, 6.4**

### Property 8: Vector store round-trip and family-scoped retrieval

For any family group with indexed receipts and any query, RAG retrieval SHALL return only receipts belonging to that family group, ordered by embedding similarity, and SHALL never return a receipt from a different family group even when that receipt is more similar to the query. Indexing a receipt and then retrieving by a matching query SHALL return it.

**Validates: Requirements 5.2, 6.5**

### Property 9: LLM provider selection, switching, and fallback

For any combination of `SARVAM_API_KEY` and `GROQ_API_KEY`:
- When both keys are configured, `providers(preferred)` SHALL return both providers with the preferred one first (or Sarvam-first by default), and `answer()` SHALL use the preferred provider first and automatically retry with the other on API error, rate limit, or timeout.
- When only one key is configured, `providers()` SHALL return that provider alone and no fallback occurs.
- When neither key is configured, `providers()` SHALL raise an error stating no LLM provider is available.
- When both providers fail, `answer()` SHALL raise a 502 error describing both failures.
- (Provider SDKs are mocked; fallback is triggered by injecting a mock API error on the first provider.)

**Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7**

### Property 10: Chat loop tool grounding and response scope

For any authenticated chat, the response SHALL carry the member's identity and `family_group_id`, and for any tool call the LLM emits, the executed result SHALL equal the underlying analytics function computed over the resolved (family, member) scope — so every figure in the answer traces to a tool result, not an LLM estimate. (The LLM is mocked to emit tool calls deterministically.)

**Validates: Requirements 4.1, 4.5, 4.6, 8.4**

### Property 11: Chat member-scope resolution

For any logged-in member and roster, a question naming a specific family member SHALL resolve tool scope to that member (and reject names outside the roster); a first-person question SHALL resolve to the logged-in member; and a whole-family question SHALL resolve to no member filter. `family_group_id` SHALL always remain the requester's, never taken from the question or LLM.

**Validates: Requirements 4.2, 4.3, 4.4**

### Property 12: RAG context injection

For any fuzzy/item-level question for which RAG retrieval returns receipts, those receipts SHALL be present in the tool result fed back to the LLM (enabling the answer to reference them by merchant/date/items). (The LLM choice to call `search_receipts` is mocked; the embed service is mocked.)

**Validates: Requirements 5.1, 5.3, 11.1**

### Property 14: Automatic provider fallback

For any configuration where both provider keys are present and the primary provider raises an API error, rate-limit error, or timeout, the chatbot SHALL complete the request using the secondary provider and SHALL include `provider_used` naming the fallback in the response. When both providers raise such errors, the chatbot SHALL return a 502 error describing both failures and SHALL not return an answer. (Both provider SDKs are mocked; fallback is triggered by injecting a mock error on the primary provider.)

**Validates: Requirements 7.5, 7.6**



For any chat where neither tool calls nor RAG retrieval return supporting data, the chatbot SHALL respond that no information is available, and SHALL produce no spending figures.

**Validates: Requirements 4.6, 11.2**

## 9. Testing Strategy

**Dual approach — unit/property tests are complementary.**

**Property-based tests** (≥100 iterations each; tag `**Feature: rag-chatbot-family-accounts, Property N: <text>**`):
- Properties 1–6, 9, 13: pure backend logic, no external calls — straightforward PBT over generated families/members/receipts/env.
- Properties 7–8, 12: mock `embeddings.embed` with a deterministic vector (e.g., hashed text → fixed-dim pseudo-vector) so the vector store and RAG plumbing are exercised without Sarvam; the mock must be dimension-stable (768).
- Properties 10–11: mock the LLM client with a deterministic script that emits chosen tool calls and a final answer; assert the chat loop executes tools with the resolved scope and grounds the answer.

**Unit tests (examples / edge cases)**:
- UI presence: Chat tab renders input + conversation (Req 9.1–9.3); member filter on Dashboard/Explore/Chat populated from roster and defaulting to logged-in member (Req 10.1/10.2); roster dropdown on Review (Req 2.1).
- Edge cases: confirm with no member selected → confirmer label; delete a multi-receipt image's last receipt; join with a bad code → 404; create when already in a group → 409.

**Integration tests (1–3 examples, real services, not PBT)**:
- Real Sarvam (or Groq) chat answers a budget question using an actual tool call (Req 4.1) and cites a receipt found via real embeddings (Req 5.1/11.1). Spot-check only — LLM behavior is non-deterministic.
- `POST /api/chat` without a cookie → 401; with a cookie → 200 scoped to the family (Req 8.1/8.3).

**Smoke tests (single execution)**:
- `receipt_embeddings` is a vec0 table in `receiptiq.db` and sqlite-vec loads on startup (Req 6.1).
- `python backend/synth.py` can seed a multi-member family group (optional enhancement to the "Sharma Family" persona) for manual/demo verification of family-wide visibility and member filtering.

**Dependency additions** (`backend/requirements.txt`): `sqlite-vec`, `groq` (Sarvam's `sarvamai` is already present).
