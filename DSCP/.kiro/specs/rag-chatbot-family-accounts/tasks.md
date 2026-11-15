# Implementation Plan: Family Accounts & RAG Chatbot

## Overview

This plan implements two capabilities in the existing FastAPI + SQLite + React codebase, building incrementally on the current `db.py` schema/migration pattern and the existing `auth.py` / `app.py` / `analysis.py` / `insights.py` modules.

1. **Family accounts** — separate member logins share one Family Group. Receipts stay attributed to the member who made the purchase (Member Label, set at review time) but are visible family-wide. All data access is re-scoped from `user_id` to the requester's `family_group_id`, with an optional member filter.
2. **RAG chatbot** — a hybrid assistant combining tool-calling (exact figures via the existing `budget_status` / `insights` / `analysis` functions) with retrieval-augmented generation (fuzzy, item-level search over receipt embeddings stored in `receiptiq.db` via sqlite-vec). Both Sarvam and Groq are supported simultaneously when both keys are configured: the user can select the preferred provider via a dropdown in the Chat UI, and if the selected provider fails the system automatically retries with the other before returning an error; no OpenAI.

Implementation order: backend database foundation → embeddings + vector store → family API → re-scope data access + embedding hooks → chat backend + endpoint → frontend chat/filter UI → frontend member-label/filtered views → synthetic multi-member seeding. Property-based tests are placed next to the implementation they verify (the design defines 13 correctness properties). The embedding and LLM services are mocked in tests for determinism.

## Tasks

- [x] 1. Backend database foundation and migration
  - [x] 1.1 Add vector-store and LLM dependencies to `backend/requirements.txt`
    - Append `sqlite-vec` and `groq` to `backend/requirements.txt` (the `sarvamai` package is already present)
    - Install the new packages so `sqlite_vec` imports resolve
    - _Requirements: 6.1, 7.1_

  - [x] 1.2 Extend `db.py` schema, `connect()`, and constants for family groups + embeddings
    - Add the `family_groups` table (id, name, unique `join_code`, `created_by`, `created_at`) to `SCHEMA`
    - Append to `ADDED_COLUMNS`: `receipts.family_group_id` (integer), `receipts.member_id` (integer), `users.family_group_id` (integer)
    - Export a public `EMBED_DIM = 768` constant so `vectorstore` and the vec0 table agree
    - Load the `vec0` extension on every connection inside `connect()` via `sqlite_vec.load(con)` (the codebase opens a fresh connection per request)
    - _Requirements: 1.4, 6.1 · Design §3.1, §3.2, §3.5_

  - [x] 1.3 Implement `migrate()` backfill and create the `receipt_embeddings` vec0 table
    - Backfill each pre-existing user without a `family_group_id` into a one-member family group (random `join_code`); set `users.family_group_id`
    - Backfill `receipts.family_group_id` from the uploader's group; set `receipts.member_id = user_id` for already-confirmed receipts (the only member known at migration time)
    - Keep the existing image-migration step intact; backfills must be idempotent and never delete rows
    - After schema/column work, create the `receipt_embeddings` vec0 virtual table with `receipt_id integer primary key`, `family_group_id integer partition key`, `embedding float[768]`
    - _Requirements: 1.6 · Design §3.3, §3.4_

  - [ ]* 1.4 Write property test for migration preservation
    - **Property 3: Migration preserves data and backfills family structures**
    - **Validates: Requirements 1.6**
    - Tag `**Feature: rag-chatbot-family-accounts, Property 3: Migration preserves data and backfills family structures**`; ≥100 iterations over generated pre-existing DBs (users/receipts/items, plus empty and pending-only cases). Assert existing row counts are unchanged, every user gets a `family_group_id`, and every confirmed receipt gets a `member_id`.

- [x] 2. Embeddings and vector store
  - [x] 2.1 Create `embeddings.py` (Sarvam EmbeddingGemma 2)
    - `available()` returns `bool(SARVAM_API_KEY)`; `embed(texts)` calls the Sarvam embeddings endpoint (`model="embeddinggemma-2"`, `/v2/embeddings`, same `SARVAM_API_KEY` as OCR) and returns a list of 768-dim normalized vectors
    - `receipt_text(r, items)` builds the embedded text from merchant, date, category, item names, and raw OCR text
    - Handle both SDK attribute and dict response shapes; normalize vectors so dot product = cosine similarity
    - _Requirements: 6.2 · Design §4.2_

  - [x] 2.2 Create `vectorstore.py` (sqlite-vec operations)
    - `upsert(receipt_id, family_group_id, vector)`: delete-then-insert the embedding (used on confirm + edit)
    - `remove(receipt_id)`: delete the embedding row
    - `retrieve(query_vec, family_group_id, k=8)`: family-scoped KNN using the `family_group_id = ?` partition-key equality constraint so no other family's receipt is ever returned
    - Add a `_vec` serialization helper for the JSON/binary form sqlite-vec accepts; import `EMBED_DIM`/`connect` from `db`
    - _Requirements: 5.2, 6.2, 6.3, 6.4, 6.5 · Design §4.3_

  - [ ]* 2.3 Write property test for vector store round-trip and family-scoped retrieval
    - **Property 8: Vector store round-trip and family-scoped retrieval**
    - **Validates: Requirements 5.2, 6.5**
    - Mock `embeddings.embed` with a dimension-stable (768) deterministic pseudo-vector (e.g., hash → fixed-dim). Assert retrieval returns only the queried family's receipts ordered by similarity, never a more-similar receipt from another family; indexing then retrieving by a matching query returns it.

- [x] 3. Family group API
  - [x] 3.1 Create `family.py` (APIRouter prefix `/api/family`)
    - `POST /api/family` (create): reject 409 if the user already has a `family_group_id`; generate a `join_code`; add the creator as the first member; return `family_info` (`{id, name, join_code, members}`)
    - `POST /api/family/join`: look up by `join_code` (404 if absent); reject 409 if already in any group or already in this one; add the user to the group
    - `GET /api/family/roster`: return `[{id, name, email}]` for all users sharing the requester's `family_group_id` (404 if the user has no group)
    - Reuse `auth.current_user`; open a fresh `connect()` per request following the existing pattern
    - _Requirements: 1.2, 1.3, 1.4, 1.5 · Design §4.1_

  - [ ]* 3.2 Write property test for roster completeness and membership
    - **Property 2: Roster completeness & membership**
    - **Validates: Requirements 1.2, 1.3, 1.4, 1.5**
    - Assert the roster equals exactly the users carrying that `family_group_id`; creating a group makes the creator the sole first member; a non-member joining increases the roster by exactly one and contains the joiner; all persisted across fresh connections.

  - [ ]* 3.3 Write property test for signup round-trip
    - **Property 1: Signup round-trip**
    - **Validates: Requirements 1.1**
    - Assert any valid signup (non-empty name, valid email, password ≥ 8 chars, unique email) creates a persisted, retrievable individual account, and a duplicate email is rejected.

- [ ] 4. Re-scope backend data access and embedding hooks
  - [x] 4.1 Extend `analysis.py` scoping from `user_id` to family group
    - Change the `analysis()` base query from `where user_id = ?` to `where family_group_id = ?` and accept an optional `member_filter` (when set, add `and member_id = ?`)
    - Update `changes()` and the `whole`-period share query to use the same family/member scoping; keep the pure-function return shape unchanged so `/api/analysis` and the chat `get_spending` tool both work
    - _Requirements: 3.2, 3.3, 3.4 · Design §4.4, §4.5_

  - [ ] 4.2 Re-scope `app.py` read endpoints to family group with optional member filter
    - Add a `fg_scope(user, member=None)` helper returning a `where family_group_id = ? and status = 'confirmed'` clause plus optional `member_id = ?`
    - Re-scope `get_receipt(con, rid, user)` to family-wide visibility (404 for other families); `list_receipts` accepts `?member=<uid>`; `/api/stats` builds its queries over the family-scoped confirmed receipts and accepts `?member=`; `/api/analysis` accepts `?member=` forwarded to `analysis()`; `/api/images` checks the image's family
    - `current_user` already yields `family_group_id`, so derive scope server-side from the session, never from client input
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5 · Design §4.5_

  - [ ] 4.3 Add embedding hooks and member-label assignment in `app.py`
    - In `update_receipt`, when `body.status == "confirmed"`: set `member_id` to the selected member or default to the confirming member (`user["id"]`); if `embeddings.available()`, embed `receipt_text(r, items)` and `vectorstore.upsert(rid, user["family_group_id"], vec)`
    - On every confirm/edit of a confirmed receipt, regenerate the embedding (upsert overwrites)
    - In `delete_receipt`, call `vectorstore.remove(rid)` before deleting the receipt row
    - Accept `member` in the `ReceiptIn`/PUT flow so the Review UI can send the chosen label
    - _Requirements: 2.2, 2.3, 2.4, 6.2, 6.3, 6.4 · Design §4.5_

  - [ ]* 4.4 Write property test for member attribution invariant
    - **Property 4: Member attribution invariant**
    - **Validates: Requirements 2.2, 2.3, 2.4**
    - Assert confirming stores a Member Label equal to the selection when chosen, else the confirming member's id; every confirmed receipt carries a non-null `member_id` referencing a user in the receipt's family group.

  - [ ]* 4.5 Write property test for family isolation
    - **Property 5: Family isolation — no cross-family leakage**
    - **Validates: Requirements 3.1, 3.4, 3.5, 8.2**
    - Assert a member of one family never receives, via `/api/receipts`, `/api/stats`, `/api/analysis`, `/api/images`, or any chat tool, any receipt or figure derived from another family's receipts, regardless of request parameters.

  - [ ]* 4.6 Write property test for member filter correctness
    - **Property 6: Member filter correctness**
    - **Validates: Requirements 3.2, 3.3, 10.3, 10.4**
    - Assert every returned receipt/row carries the filtered member's label and the set is complete; with no filter, all family receipts are included.

  - [ ]* 4.7 Write property test for embedding lifecycle
    - **Property 7: Embedding lifecycle**
    - **Validates: Requirements 6.2, 6.3, 6.4**
    - Mock `embeddings.embed` deterministically. Assert confirming creates an embedding row, editing a confirmed receipt regenerates it, deleting removes it, and no embedding row survives its receipt.

- [ ] 5. Chat backend and chat endpoint
  - [ ] 5.1 Create `chat.py` (dual-provider selection/fallback, scope resolution, tools, chat loop)
    - `providers(preferred=None)`: return an ordered list of available providers — preferred first (if present and available), the other second; raise 503 when no key is configured
    - `answer(user, message, history, member_filter, preferred_provider=None)`: iterate the provider list; for each provider attempt `_answer_with(prov, ...)`; catch API errors, rate-limit errors, and timeouts and continue to the next provider; raise 502 when all providers fail
    - `_answer_with(prov, ...)`: the per-provider chat loop — scope resolution, tool calls (≤ `MAX_ROUNDS`), grounding guard; returns `chat_response` including `provider_used`
    - `scope(user, member_ref, roster)`: resolve `(family_group_id, member_id)` — `family_group_id` is always the requester's; `None`/`"all"`/`"family"` → whole family; `"me"`/`"my"`/own name → logged-in member; a roster name → that member; unknown name → reject
    - Tool wrappers always scoped server-side to the requesting family: `get_budget`, `get_spending`, `get_insights`, and `search_receipts` (RAG via `vectorstore.retrieve` then load full receipts for citation)
    - Grounding guard: returns `REFUSAL` when no tool/RAG returned data, overriding fabricated figures
    - Wrap Sarvam (Token Factory, OpenAI-compatible tool calling via `sarvamai`) and Groq (`groq`) each behind a thin adapter exposing the same `chat(messages, tools)` interface; map `ApiError`/upstream errors to readable 502/400
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 5.1, 5.2, 5.3, 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 11.1, 11.2 · Design §4.4_

  - [ ] 5.2 Add the `/api/chat` endpoint, `/api/providers` endpoint, and include the family router in `app.py`
    - `GET /api/providers` (auth via `current_user`): return the ordered list from `chat.providers()` so the frontend can conditionally render the provider dropdown without sending a chat message first
    - `POST /api/chat` (auth via `current_user`): accept `{message, member_id?, provider?}`; call `chat.answer(user, body.message, body.history or [], body.member_id, body.provider)`; return `{answer, member:{id,name}, family_group_id, receipts?[], provider_used}` so every answer carries member identity, family scope, and the provider that actually answered
    - Reject unauthenticated requests with 401 (the `current_user` dependency already does this); surface the no-family-group guidance, no-provider 503 messages, and both-providers-failed 502 from `chat`
    - Register the family router (`app.include_router(family.router)`) so `/api/family/*` is served
    - _Requirements: 1.2, 1.3, 1.5, 7.2, 7.3, 7.5, 7.6, 7.7, 8.1, 8.2, 8.3, 8.4 · Design §4.5, §6_

  - [ ]* 5.3 Write property test for LLM provider selection, switching, and fallback
    - **Property 9: LLM provider selection, switching, and fallback**
    - **Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7**
    - Over all combinations of `SARVAM_API_KEY`/`GROQ_API_KEY` (mocked env) and `preferred` values:
      - When both keys configured: `providers(preferred)` returns both with preferred first; `answer()` calls the preferred provider first and falls back to the other on a mocked API error; `provider_used` in the response reflects the fallback provider.
      - When only one key: `providers()` returns that provider; no fallback attempt is made.
      - When neither key: `providers()` raises 503.
      - When both providers raise mocked API errors: `answer()` raises 502 with a combined error.

  - [ ]* 5.4 Write property test for provider fallback behavior
    - **Property 14: Automatic provider fallback**
    - **Validates: Requirements 7.5, 7.6**
    - Mock the first provider to always raise an `APIError`; assert `answer()` completes successfully using the second provider and `provider_used` names the fallback. Mock both providers to raise; assert `answer()` raises 502 describing both failures. Assert at least one complete answer is produced (no fabrication) when the fallback provider returns valid tool-call responses.

  - [ ]* 5.4 Write property test for chat member-scope resolution
    - **Property 11: Chat member-scope resolution**
    - **Validates: Requirements 4.2, 4.3, 4.4**
    - Assert a named family member resolves to that member (roster outsiders rejected), a first-person reference resolves to the logged-in member, a whole-family reference resolves to no filter, and `family_group_id` always stays the requester's.

  - [ ]* 5.5 Write property test for chat loop tool grounding and response scope
    - **Property 10: Chat loop tool grounding and response scope**
    - **Validates: Requirements 4.1, 4.5, 4.6, 8.4**
    - Mock the LLM to emit chosen tool calls deterministically. Assert the response carries the member's identity and `family_group_id`, and each executed tool result equals the analytics function over the resolved (family, member) scope, so every figure traces to a tool result.

  - [ ]* 5.6 Write property test for RAG context injection
    - **Property 12: RAG context injection**
    - **Validates: Requirements 5.1, 5.3, 11.1**
    - Mock the LLM to call `search_receipts` and mock the embed service. Assert receipts returned by family-scoped RAG retrieval are present in the tool result fed back to the LLM (enabling citation by merchant/date/items).

  - [ ]* 5.7 Write property test for no-data refusal
    - **Property 13: No-data refusal**
    - **Validates: Requirements 4.6, 11.2**
    - Mock the LLM with no tool calls / empty RAG results. Assert the chatbot responds that no information is available and produces no spending figures.

- [ ] 6. Checkpoint — backend layer complete
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 7. Frontend chat UI and member filter
  - [x] 7.1 Create `MemberFilter.jsx` (shared component)
    - A roster dropdown with an explicit "All family" / "Everyone" option; clearing the selection shows the whole family
    - Props: `roster`, `value`, `onChange`, `me`; marks the logged-in member with "(you)"
    - _Requirements: 10.1, 10.3, 10.4 · Design §5.4_

  - [ ] 7.2 Create `Chat.jsx` (chat UI with provider selector)
    - Fetch the roster (`/family/roster`, fall back to `[me]`); default the member filter to the logged-in member
    - On mount, probe available providers (from a lightweight `/api/providers` endpoint or the first `/api/chat` response header); store `availableProviders` in state
    - Render a provider dropdown (`<select>` over `availableProviders`) only when `availableProviders.length > 1`; default to the first (Sarvam when both keys are configured)
    - Hold the conversation (`messages`) in component state for the browser session; render the conversation and an input
    - On submit, POST `{message, member_id, provider?}` to `/api/chat` via `api.js` (omit `provider` when only one is available); append the returned `answer`; optionally show `provider_used` as a small label per assistant message
    - Render `MemberFilter` above the conversation
    - _Requirements: 7.2, 7.3, 9.1, 9.2, 9.3, 10.1, 10.2 · Design §5.2_

  - [ ] 7.3 Add the Chat tab in `App.jsx`
    - Add `["chat","Chat"]` to `TABS`; keep `PAGES` in sync; render `<Chat me={me} />` when `page === "chat"`
    - _Requirements: 9.1 · Design §5.1_

  - [ ]* 7.4 Write unit tests for the Chat UI
    - Assert the Chat tab renders an input + conversation, the member filter is populated from the roster and defaults to the logged-in member, and submitting calls `/api/chat` and displays the answer
    - Assert the provider dropdown is rendered when `availableProviders` has two entries and hidden when only one; selecting a provider sends it in the POST body
    - _Requirements: 7.2, 7.3, 9.1, 9.2, 9.3, 10.1, 10.2_

- [ ] 8. Frontend member label and filtered views
  - [ ] 8.1 Add the member label dropdown to `Review.jsx`
    - Fetch the roster on mount; add a "Spent by" select to each `ReceiptCard` populated from the roster, defaulting to the logged-in member
    - On confirm, include `member` in the PUT body (default to the confirmer when none selected)
    - _Requirements: 2.1, 2.3 · Design §5.3_

  - [ ] 8.2 Add the member filter to `Dashboard.jsx`
    - Render `MemberFilter` (default to logged-in member); pass the selected `member` to `/api/stats` and refetch on change; clearing the filter fetches the whole family
    - _Requirements: 10.1, 10.3, 10.4 · Design §5.4_

  - [ ] 8.3 Add the member filter to `Explore.jsx`
    - Render `MemberFilter`; pass the selected `member` to `/api/analysis` alongside the existing category/merchant/month params and refetch on change
    - _Requirements: 10.1, 10.3, 10.4 · Design §5.4_

  - [ ]* 8.4 Write unit tests for the member label and filtered views
    - Assert the Review card shows a roster dropdown defaulting to the logged-in member and sends `member` on confirm; Dashboard and Explore show the member filter, default to the logged-in member, and pass the selection to their API calls
    - _Requirements: 2.1, 10.1, 10.2, 10.3, 10.4_

- [ ] 9. Synthetic multi-member family seeding
  - [ ] 9.1 Enhance `synth.py` to optionally seed multi-member family groups
    - Extend the demo loading path (e.g., the "Sharma Family" persona) to create a `family_groups` row and add multiple member users sharing it, attributing a subset of receipts to each member via `member_id`
    - Keep the existing single-account personas and `--load`/`--csv`/`--check` behavior intact; the multi-member seeding is additive and idempotent
    - _Requirements: 1.2, 1.4, 2.4 · Design §9 (smoke)_

- [ ] 10. Final checkpoint — all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional (property/unit tests) and can be skipped for a faster MVP, but each property test maps directly to a design correctness property and a requirement.
- Each implementation task references specific requirements and design sections for traceability; the design document holds the detailed contracts and code sketches.
- Checkpoints validate incrementally: after the backend layer and again at the end.
- Property-based tests use ≥100 iterations with the embedding service (`embeddings.embed`) and LLM client mocked for determinism; the mock embeddings must be dimension-stable (768). Each property test is its own file tagged `**Feature: rag-chatbot-family-accounts, Property N: <text>**`.
- Backend is Python/FastAPI; frontend is JavaScript/React (JSX) — the implementation language was fixed by the existing codebase, so no language selection is needed.
- `family_group_id` is always derived server-side from the authenticated session; the LLM and the client may only choose a member, never a family group.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1", "7.1"] },
    { "id": 1, "tasks": ["1.2"] },
    { "id": 2, "tasks": ["1.3", "2.2", "3.1", "4.1"] },
    { "id": 3, "tasks": ["1.4", "2.3", "3.2", "3.3", "4.2", "5.1", "8.1", "9.1"] },
    { "id": 4, "tasks": ["4.3", "4.5", "4.6", "5.3", "5.4", "5.5", "5.6", "5.7", "8.2", "8.3"] },
    { "id": 5, "tasks": ["4.4", "4.7", "5.2", "8.4"] },
    { "id": 6, "tasks": ["7.2"] },
    { "id": 7, "tasks": ["7.3", "7.4"] }
  ]
}
```
