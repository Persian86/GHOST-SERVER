"""Household memory and standing rules — one store, shared by every agent."""
from __future__ import annotations

from . import Ctx, tool
from .. import store


@tool("remember", """Save a durable fact about the user or household so EVERY Ghost device knows it
(e.g. 'Zoey's birthday is 12 May', 'Adam takes his coffee black'). Optional section: People, Home,
Work, Taste, Health, Routine, Notes.""",
      {"type": "OBJECT", "properties": {
          "fact": {"type": "STRING", "description": "The fact, as a short sentence"},
          "section": {"type": "STRING", "description": "People | Home | Work | Taste | Routine | Notes"}},
       "required": ["fact"]})
def remember(args: dict, ctx: Ctx) -> str:
    ok = store.add_fact(args.get("fact", ""), args.get("section") or "Notes", ctx.device_name)
    return "Saved — every Ghost device knows it now." if ok else "I already knew that."


@tool("recall", """Look up what Ghost knows about the user or household (shared memory) by keywords.
Use before saying you don't know something personal.""",
      {"type": "OBJECT", "properties": {"query": {"type": "STRING"}}, "required": ["query"]})
def recall(args: dict, ctx: Ctx) -> str:
    words = [w for w in str(args.get("query", "")).lower().split() if len(w) > 2]
    hits = [f"[{f['section']}] {f['text']}" for f in store.facts()
            if not words or any(w in f["text"].lower() for w in words)]
    return "\n".join(hits[:20]) if hits else "Nothing in shared memory about that."


@tool("forget", "Remove facts from shared memory that contain these words.",
      {"type": "OBJECT", "properties": {"match": {"type": "STRING"}}, "required": ["match"]})
def forget(args: dict, ctx: Ctx) -> str:
    n = store.forget_facts(str(args.get("match", "")).strip()) if args.get("match") else 0
    return f"Forgot {n} fact(s)." if n else "Nothing matched."


@tool("standing_rule", """Add, list or remove a standing instruction for how Ghost behaves on every device
('from now on always use Celsius', 'never read news about sport').""",
      {"type": "OBJECT", "properties": {
          "action": {"type": "STRING", "description": "add | list | remove"},
          "rule": {"type": "STRING", "description": "Rule text, or words/id to remove"}},
       "required": ["action"]})
def standing_rule(args: dict, ctx: Ctx) -> str:
    act = str(args.get("action", "list")).lower()
    if act == "add":
        return "Learned — every device follows it from its next session." if store.add_rule(args.get("rule", "")) \
            else "I already follow that rule."
    if act in ("remove", "forget", "delete"):
        n = store.remove_rules(str(args.get("rule", "")))
        return f"Removed {n} rule(s)." if n else "No rule matched."
    rules = store.rules()
    return "; ".join(f"[{r['id']}] {r['text']}" for r in rules) or "No standing rules yet."
