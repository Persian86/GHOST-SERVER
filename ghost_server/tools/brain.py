"""Claude, available to every agent as a tool."""
from __future__ import annotations

from . import Ctx, tool
from .. import store

_SYSTEM = ("You are the deep-reasoning core of Ghost, a household voice assistant. Think the problem "
           "through, then answer clearly. The answer is read aloud or summarised, so lead with the "
           "conclusion and avoid tables and heavy markdown.")


def claude(model: str | None = None, fast: bool = False):
    """Ghost's brain: Claude, with the local model on the AI PC as the offline
    backup (see local_llm.py). Same complete() contract either way."""
    from ..claude import AnthropicLLM
    from ..local_llm import ResilientLLM, local_from_settings
    key = store.get("anthropic_api_key")
    mode = store.get("local_llm_mode") or "fallback"
    if store.get("unplugged"):
        mode = "only"                      # Ghost Unplugged: nothing goes to the cloud
    local = local_from_settings(store)
    if not key and local is None:
        raise RuntimeError("Claude isn't set up — add the Anthropic key on the Ghost Server admin page")
    primary = AnthropicLLM({"api_key": key, "routing": "",
                            "model": model or (store.get("claude_fast_model") if fast else store.get("claude_model"))}) \
        if key else None
    return ResilientLLM(primary, local, mode)


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


@tool("unplugged_mode", """Ghost Unplugged: switch Ghost off the cloud (no Claude, no Gemini) so it thinks with the local
model and hears/speaks with Whisper and Piper on the AI PC — or back on again. 'go unplugged', 'unplugged mode on',
'use only the local AI', 'plug back in', 'are you unplugged?'.""",
      {"type": "OBJECT", "properties": {"action": {"type": "STRING", "description": "on | off | status"}},
       "required": ["action"]})
def unplugged_mode(args: dict, ctx: Ctx) -> str:
    from .. import local_voice
    act = str(args.get("action") or "status").lower()
    if act in ("on", "start", "enable"):
        if not (store.get("local_llm_url") or "").strip():
            return "I can't go unplugged yet — the local model's address isn't set on the admin page."
        store.put("unplugged", True)
        from .. import modes
        modes.broadcast()
        return ("I'm unplugged now: thinking, hearing and speaking all stay on the AI PC. This conversation finishes "
                "on the cloud voice; the next one uses the local voice. Say 'plug back in' to go back.")
    if act in ("off", "stop", "disable"):
        store.put("unplugged", False)
        from .. import modes
        modes.broadcast()
        return "Plugged back in — Claude and Gemini are back from the next conversation."
    st = local_voice.status()
    return ("I'm unplugged — everything runs on the AI PC." if st["unplugged"]
            else "I'm plugged in: Claude thinks and Gemini talks, with the local model as the backup.")
