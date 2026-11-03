"""Claude, available to every agent as a tool."""
from __future__ import annotations

from . import Ctx, tool
from .. import store

_SYSTEM = ("You are the deep-reasoning core of Ghost, a household voice assistant. Think the problem "
           "through, then answer clearly. The answer is read aloud or summarised, so lead with the "
           "conclusion and avoid tables and heavy markdown.")


def claude(model: str | None = None, fast: bool = False):
    from ..claude import AnthropicLLM
    key = store.get("anthropic_api_key")
    if not key:
        raise RuntimeError("Claude isn't set up — add the Anthropic key on the Ghost Server admin page")
    return AnthropicLLM({"api_key": key, "routing": "",
                         "model": model or (store.get("claude_fast_model") if fast else store.get("claude_model"))})


@tool("think_deeply", """Send a hard question to Ghost's deep-reasoning brain (Claude) on the home server.
Use for multi-step reasoning, planning, maths, debugging, comparisons, writing — anything where a quick
answer could be wrong. Include all context. Not for simple facts or device control.""",
      {"type": "OBJECT", "properties": {
          "question": {"type": "STRING"}, "context": {"type": "STRING"},
          "effort": {"type": "STRING", "description": "normal | max"}},
       "required": ["question"]})
def think_deeply(args: dict, ctx: Ctx) -> str:
    q = str(args.get("question", "")).strip()
    if not q:
        return "No question was given."
    model = "claude-opus-5-5" if str(args.get("effort", "")).lower() == "max" else None
    text, _ = claude(model).complete(
        [{"role": "system", "content": _SYSTEM},
         {"role": "user", "content": q + (f"\n\nContext:\n{args['context']}" if args.get("context") else "")}],
        timeout=180, max_tokens=6000)
    return text.strip() or "Claude returned nothing."
