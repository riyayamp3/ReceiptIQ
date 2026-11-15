"""RAG chatbot: dual-provider LLM (Sarvam | Groq), tool calling, and grounding guard.

Public API
----------
providers(preferred=None) -> list[str]
    Returns the ordered list of configured providers. Raises 503 when none is set.

answer(user, message, history, member_filter, preferred_provider=None) -> dict
    Runs the chat loop with automatic provider fallback. Returns
    {answer, member:{id,name}, family_group_id, receipts, provider_used}.

scope(user, member_ref, roster) -> (family_group_id, member_id | None)
    Resolves a member reference from chat context into a (fgid, mid) pair.

Design refs: §4.4 (chat loop), §4.1 (family API), §4.5 (tool wrappers)
Requirements: 4.1–4.6, 5.1–5.3, 7.1–7.7, 11.1–11.2
"""
import json
import os
from contextlib import closing
from datetime import date

from fastapi import HTTPException

import embeddings as _embeddings
import vectorstore
from analysis import analysis
from db import connect
from insights import budget_status, insights

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_ROUNDS = 6  # maximum tool-call → result iterations before the guard fires

REFUSAL = (
    "I don't have enough information in your receipts to answer that. "
    "Please add and confirm some receipts first, or try rephrasing your question."
)

# ---------------------------------------------------------------------------
# Provider management (Req 7.1–7.7)
# ---------------------------------------------------------------------------


def providers(preferred: str | None = None) -> list[str]:
    """Return an ordered list of available LLM providers.

    When *preferred* is given and that provider's key is configured, it is
    placed first; the other (if configured) follows as the automatic fallback.
    Raises 503 when no provider key is configured (Req 7.7).
    """
    available: list[str] = []
    if os.getenv("SARVAM_API_KEY"):
        available.append("sarvam")
    if os.getenv("GROQ_API_KEY"):
        available.append("groq")

    if not available:
        raise HTTPException(
            503,
            "No LLM provider is configured. Set SARVAM_API_KEY or GROQ_API_KEY.",
        )

    if preferred and preferred in available:
        # Move the preferred provider to the front; the other follows as fallback.
        available = [preferred] + [p for p in available if p != preferred]

    return available


# ---------------------------------------------------------------------------
# Thin provider adapters — both expose chat(messages, tools) → response object
# ---------------------------------------------------------------------------
#
# response object duck-type:
#   .content          str | None      — final text answer
#   .tool_calls       list | None     — list of tool-call objects
#
# tool_call duck-type:
#   .id               str
#   .function.name    str
#   .function.arguments  str (JSON)


class _SarvamAdapter:
    """Calls the Sarvam chat API directly (the SDK does not expose chat.completions)."""

    MODEL = os.getenv("SARVAM_CHAT_MODEL", "sarvam-105b")
    BASE_URL = "https://api.sarvam.ai/v1"

    def chat(self, messages, tools):
        import requests
        headers = {"api-subscription-key": os.environ["SARVAM_API_KEY"]}
        body = {"model": self.MODEL, "messages": messages, "temperature": 0.2}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        resp = requests.post(
            f"{self.BASE_URL}/chat/completions", json=body, headers=headers, timeout=60
        )
        resp.raise_for_status()
        return resp.json()  # dict, not SDK object


class _GroqAdapter:
    """Wraps the Groq SDK. Returns a dict for consistency with _SarvamAdapter."""

    MODEL = os.getenv("GROQ_CHAT_MODEL", "llama-3.3-70b-versatile")

    def chat(self, messages, tools):
        from groq import Groq
        client = Groq(api_key=os.environ["GROQ_API_KEY"])
        kwargs = {"model": self.MODEL, "messages": messages}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        resp = client.chat.completions.create(**kwargs)
        # Convert SDK object to dict
        if hasattr(resp, "model_dump"):
            return resp.model_dump()
        import json
        return json.loads(resp.model_dump_json())


def _get_adapter(prov: str):
    if prov == "sarvam":
        return _SarvamAdapter()
    if prov == "groq":
        return _GroqAdapter()
    raise ValueError(f"Unknown provider: {prov!r}")


def _llm_chat(prov: str, messages: list[dict], tools: list[dict]):
    """Call the given provider and return the first choice's message (as a dict)."""
    resp = _get_adapter(prov).chat(messages, tools)
    return resp["choices"][0]["message"]


# ---------------------------------------------------------------------------
# Scope resolution (Req 4.2–4.4)
# ---------------------------------------------------------------------------


def scope(user: dict, member_ref, roster: list[dict]) -> tuple:
    """Resolve a member reference into (family_group_id, member_id | None).

    *family_group_id* is ALWAYS the requester's — the client/LLM cannot change it.
    *member_ref* is whatever the LLM passed as a member argument (may be None,
    a name string, or one of the sentinel values).
    """
    fgid = user["active_group_id"]

    # Whole-family scope
    if member_ref in (None, "", "all", "family", "everyone", "whole family"):
        return fgid, None

    # First-person references → the logged-in user
    if isinstance(member_ref, str) and member_ref.lower() in (
        "me", "my", "mine", "i", user["name"].lower()
    ):
        return fgid, user["id"]

    # Named member lookup (case-insensitive)
    if isinstance(member_ref, str):
        match = next(
            (m for m in roster if m["name"].lower() == member_ref.lower()), None
        )
        if match:
            return fgid, match["id"]
        raise ValueError(f"Unknown family member: {member_ref!r}")

    # Numeric member id passed directly (from member_filter parameter)
    if isinstance(member_ref, int):
        match = next((m for m in roster if m["id"] == member_ref), None)
        if match:
            return fgid, match["id"]
        raise ValueError(f"Unknown member id: {member_ref!r}")

    return fgid, None  # fallback: whole family


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def _family_roster(fgid: int) -> list[dict]:
    with closing(connect()) as con:
        return [
            dict(r)
            for r in con.execute(
                "select u.id, u.name, u.email from group_members gm join users u on u.id = gm.user_id where gm.group_id = ? order by u.id",
                (fgid,),
            )
        ]


def _load_receipts(con, receipt_ids: list[int]) -> list[dict]:
    if not receipt_ids:
        return []
    placeholders = ",".join("?" * len(receipt_ids))
    rows = con.execute(
        f"select id, date, merchant, category, total, tax, raw_text from receipts where id in ({placeholders})",
        receipt_ids,
    ).fetchall()
    return [dict(r) for r in rows]


def _scoped_receipts(con, fgid: int, member_id: int | None) -> list[dict]:
    """Return all confirmed receipts in the family (optionally for one member)."""
    if member_id is not None:
        rows = con.execute(
            "select date, total, tax, category, merchant, service_type from receipts "
            "where family_group_id = ? and member_id = ? and status = 'confirmed' and total is not null",
            (fgid, member_id),
        ).fetchall()
    else:
        rows = con.execute(
            "select date, total, tax, category, merchant, service_type from receipts "
            "where family_group_id = ? and status = 'confirmed' and total is not null",
            (fgid,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Tool wrappers — always server-side scoped to the requesting family (Req 4.1)
# ---------------------------------------------------------------------------


def _tool_get_budget(con, fgid: int, member_id: int | None, budget: float | None) -> dict:
    """Return budget_status over the scoped receipts (Req 4.5)."""
    receipts = _scoped_receipts(con, fgid, member_id)
    return budget_status(receipts, budget, date.today())


def _tool_get_spending(
    con,
    fgid: int,
    member_id: int | None,
    category: str | None = None,
    merchant: str | None = None,
    month: str | None = None,
) -> dict:
    """Drill-down spending analysis (wraps analysis())."""
    return analysis(con, fgid, category, merchant, month, member_filter=member_id)


def _tool_get_insights(con, fgid: int, member_id: int | None, budget: float | None) -> list:
    """Return spending insights over the scoped receipts."""
    receipts = _scoped_receipts(con, fgid, member_id)
    return insights(receipts, budget)


def _tool_search_receipts(query: str, fgid: int) -> dict:
    """RAG retrieval: embed query → KNN → return full receipts for LLM citation (Req 5.1–5.3)."""
    if not _embeddings.available():
        return {"receipts": [], "note": "Embeddings not configured (SARVAM_API_KEY not set)."}
    try:
        vec = _embeddings.embed([query])[0]
        receipt_ids = vectorstore.retrieve(vec, fgid)
        with closing(connect()) as con:
            found = _load_receipts(con, receipt_ids)
        return {"receipts": found}
    except Exception as exc:  # noqa: BLE001
        return {"receipts": [], "error": str(exc)}


# ---------------------------------------------------------------------------
# Tool schemas (OpenAI function-calling format, understood by both providers)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_budget",
            "description": (
                "Return this calendar month's spending against the user's monthly budget "
                "and the straight-line projected end-of-month total. Use for budget questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "member": {
                        "type": "string",
                        "description": (
                            "Family member whose spending to check. "
                            "Use 'me' for the asking member, a name for a specific member, "
                            "or omit / 'all' for the whole family."
                        ),
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_spending",
            "description": (
                "Return spending totals and breakdowns, optionally filtered by category, "
                "merchant, and/or month (YYYY-MM). Use for 'how much did I spend on X' questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "member": {
                        "type": "string",
                        "description": "Family member filter ('me', a name, or omit for all).",
                    },
                    "category": {"type": "string", "description": "Spending category, e.g. 'Food & Dining'."},
                    "merchant": {"type": "string", "description": "Merchant name."},
                    "month": {"type": "string", "description": "Month as YYYY-MM, e.g. '2026-09'."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_insights",
            "description": (
                "Return short spending insights (trends, top categories, regulars, GST) "
                "for a member or the whole family. Use for 'tell me about my spending' questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "member": {
                        "type": "string",
                        "description": "Family member filter ('me', a name, or omit for all).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_receipts",
            "description": (
                "Semantically search receipts by item name, merchant, or description. "
                "Use for fuzzy questions like 'where did I buy headphones' or 'show me food bills'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural-language search query.",
                    },
                    "member": {
                        "type": "string",
                        "description": "Family member filter ('me', a name, or omit for all).",
                    },
                },
                "required": ["query"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------


def _system_prompt(user: dict, roster: list[dict]) -> dict:
    names = ", ".join(m["name"] for m in roster) or user["name"]
    return {
        "role": "system",
        "content": (
            f"You are ReceiptIQ's spending assistant for the {user['name']!r} group. "
            f"Members: {names}. "
            "Answer questions about spending by calling the provided tools — "
            "get_budget, get_spending, get_insights, or search_receipts. "
            "NEVER invent or estimate numbers; every figure MUST come from a tool result. "
            "When you cite specific receipts, mention the merchant, date, and amount. "
            "If no tool returns data relevant to the question, say you don't have enough "
            "information rather than guessing. "
            "Always answer in a friendly, concise manner."
        ),
    }


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------


def _run_tool(
    tool_call,
    user: dict,
    roster: list[dict],
    member_filter,
    con,
) -> dict:
    """Execute a single tool call and return its result dict.

    *member_filter* is the UI-level member selection (int id or None); it is used
    when the LLM omits a 'member' argument so the UI selection takes effect.
    """
    name = tool_call["function"]["name"]
    try:
        args = json.loads(tool_call["function"].get("arguments") or "{}")
    except json.JSONDecodeError:
        args = {}

    fgid = user["active_group_id"]
    budget = user.get("monthly_budget")

    # Resolve member scope: LLM arg takes priority; fall back to UI member_filter.
    llm_member_ref = args.get("member")
    if llm_member_ref:
        try:
            _, mid = scope(user, llm_member_ref, roster)
        except ValueError:
            # Unknown name: fall back to whole family and tell the LLM.
            mid = None
            args["_scope_note"] = f"Unknown member {llm_member_ref!r}; using whole family."
    elif member_filter is not None:
        try:
            _, mid = scope(user, member_filter, roster)
        except ValueError:
            mid = None
    else:
        mid = None

    if name == "get_budget":
        return _tool_get_budget(con, fgid, mid, budget)

    if name == "get_spending":
        return _tool_get_spending(
            con,
            fgid,
            mid,
            category=args.get("category"),
            merchant=args.get("merchant"),
            month=args.get("month"),
        )

    if name == "get_insights":
        return {"insights": _tool_get_insights(con, fgid, mid, budget)}

    if name == "search_receipts":
        return _tool_search_receipts(args.get("query", ""), fgid)

    return {"error": f"Unknown tool: {name!r}"}


def _has_data(result: dict) -> bool:
    """Return True when the tool result contains meaningful data."""
    if isinstance(result, list):
        return bool(result)
    if not isinstance(result, dict):
        return bool(result)
    if result.get("error"):
        return False
    # budget result
    if "spent" in result and result.get("spent", 0) > 0:
        return True
    # analysis result
    if result.get("summary", {}).get("count", 0) > 0:
        return True
    # insights result
    if result.get("insights"):
        return True
    # search_receipts result
    if result.get("receipts"):
        return True
    return False


# ---------------------------------------------------------------------------
# Chat response builder
# ---------------------------------------------------------------------------


def _chat_response(
    answer: str,
    user: dict,
    receipts_used: list[dict],
    provider_used: str,
) -> dict:
    return {
        "answer": answer,
        "member": {"id": user["id"], "name": user["name"]},
        "family_group_id": user["active_group_id"],
        "receipts": receipts_used,
        "provider_used": provider_used,
    }


# ---------------------------------------------------------------------------
# Per-provider chat loop
# ---------------------------------------------------------------------------


def _answer_with(
    prov: str,
    user: dict,
    message: str,
    history: list[dict],
    member_filter,
) -> dict:
    """Single-provider chat loop. Raises on provider-level errors so answer() can try the next one."""
    fgid = user["active_group_id"]
    if not fgid:
        return _chat_response(
            "Please join or create a group before using the chatbot.",
            user,
            [],
            prov,
        )

    roster = _family_roster(fgid)
    sys_msg = _system_prompt(user, roster)
    messages: list[dict] = [sys_msg, *history, {"role": "user", "content": message}]

    used_data = False
    receipts_used: list[dict] = []

    with closing(connect()) as con:
        for _ in range(MAX_ROUNDS):
            resp = _llm_chat(prov, messages, TOOL_SCHEMAS)

            # No tool calls → LLM wants to give a final answer
            if not resp.get("tool_calls"):
                content = resp.get("content") or ""
                if used_data:
                    return _chat_response(content, user, receipts_used, prov)
                # No data was retrieved — apply the grounding guard (Req 4.6, 11.2)
                return _chat_response(REFUSAL, user, [], prov)

            # Append the assistant turn with tool calls
            messages.append(
                {
                    "role": "assistant",
                    "content": resp.get("content"),
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["function"]["name"],
                                "arguments": tc["function"]["arguments"],
                            },
                        }
                        for tc in resp["tool_calls"]
                    ],
                }
            )

            # Execute each tool call and feed results back
            for tc in resp["tool_calls"]:
                result = _run_tool(tc, user, roster, member_filter, con)

                if _has_data(result):
                    used_data = True
                    # Collect receipt objects for citation (Req 11.1)
                    if isinstance(result, dict) and result.get("receipts"):
                        receipts_used.extend(result["receipts"])

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(result, default=str),
                    }
                )

    # Exhausted MAX_ROUNDS without a final answer
    return _chat_response(REFUSAL, user, receipts_used, prov)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def answer(
    user: dict,
    message: str,
    history: list[dict] | None = None,
    member_filter=None,
    preferred_provider: str | None = None,
) -> dict:
    """Run the chat loop with automatic provider fallback (Req 7.4–7.6).

    Iterates the ordered provider list from providers(). On provider-level
    errors (API error, rate limit, timeout) it moves on to the next provider.
    Raises 502 when all providers fail.
    """
    history = history or []
    provider_list = providers(preferred_provider)  # raises 503 when empty

    last_exc: Exception | None = None
    for prov in provider_list:
        try:
            return _answer_with(prov, user, message, history, member_filter)
        except HTTPException:
            raise  # surface 503 / auth errors directly
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            continue  # try the next provider

    raise HTTPException(
        502,
        f"All LLM providers failed. Last error: {last_exc}",
    )
