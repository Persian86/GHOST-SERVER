"""
Claude for Ghost Server (shared with Ghost Desktop's providers/llm_anthropic.py).

Claude, through Anthropic's Messages API — Ghost's primary brain.

WHY A FILE OF ITS OWN
    Everything else in this folder speaks `/v1/chat/completions`. Claude does
    not: system text is a top-level field, tool calls are `tool_use` blocks
    inside the assistant turn, tool results are `tool_result` blocks inside a
    USER turn, and images are `image` blocks rather than data-URI `image_url`s.

    The rest of the app — the pipeline engine's history and core/gemini.py's
    ladder — keeps speaking the OpenAI shape. This file translates at the edge,
    in both directions, so nothing upstream has to know which brain it is
    talking to. The class exposes exactly the same four methods as
    OpenAICompatLLM (`complete`, `chat`, `probe`, `ensure_vision_known`), which
    is the whole contract.

WHAT IT ADDS ON TOP
    Prompt caching. A voice assistant re-sends the same large system prompt and
    the same ~45 tool declarations on every single turn. Marking them with
    `cache_control` lets the API reuse them, which cuts both the cost of a turn
    and — more noticeably in a conversation — the wait before the first word.

    No new dependency: `httpx` already ships with `google-genai`.
"""
from __future__ import annotations

import json
import time
from importlib.util import find_spec
from typing import Any, AsyncIterator, Optional

from dataclasses import dataclass, field as _field


@dataclass
class FunctionCall:
    id: str = ""
    name: str = ""
    args: dict = _field(default_factory=dict)


DEFAULT_CAPS: dict = {}

API_URL = "https://api.anthropic.com/v1"
API_VERSION = "2023-06-01"

MODELS = (
    "claude-sonnet-5-5",     # the default: strong reasoning, quick enough to talk to
    "claude-haiku-5-5",      # fastest — best when latency matters most
    "claude-opus-5-5",       # hardest problems; slower
    "claude-fable-5-1",
)
DEFAULT_MODEL = MODELS[0]
FAST_MODEL = "claude-haiku-5-5"

# Words that mean a turn needs real reasoning, in whatever position. Kept short
# and English-led on purpose: routing errs toward the bigger model, so a miss
# here only costs speed, never quality.
_THINKY = ("why", "explain", "compare", "plan", "write", "code", "debug", "analy",
           "think", "should i", "how do", "how does", "how can", "help me", "summar",
           "essay", "email", "draft", "story", "calculat", "difference", "recommend",
           "review", "fix", "research", "design", "strategy", "pros and cons", "translate")


def route(messages: list[dict]) -> str:
    """'fast' for short, simple turns ("open Spotify", "what time is it",
    "turn off the lights"), 'main' for anything that needs thought.

    A turn that is continuing after tool results keeps whatever the turn began
    with — that decision belongs to the user's request, not to the tool output."""
    last_user = None
    for m in reversed(messages or []):
        if m.get("role") == "user":
            last_user = m
            break
        if m.get("role") == "tool":
            return ""            # mid-turn: caller keeps its current choice
    if last_user is None:
        return "main"
    content = last_user.get("content")
    if isinstance(content, list):
        if any(isinstance(p, dict) and p.get("type") in ("image_url", "image") for p in content):
            return "main"
        content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    text = str(content or "").lower()
    if text.startswith("[") or "system_alert" in text:
        return "main"
    words = text.split()
    if len(words) > 14 or any(k in text for k in _THINKY):
        return "main"
    return "fast"


# ─────────────────────────────────────────────────────────────────────────────
#  OpenAI shape -> Anthropic shape
# ─────────────────────────────────────────────────────────────────────────────
def _image_block(url: str) -> Optional[dict]:
    if url.startswith("data:"):
        head, _, data = url.partition(",")
        mime = head[5:].split(";")[0] or "image/png"
        return {"type": "image",
                "source": {"type": "base64", "media_type": mime, "data": data}}
    if url.startswith("http"):
        return {"type": "image", "source": {"type": "url", "url": url}}
    return None


def _content_blocks(content: Any) -> list[dict]:
    """A user/assistant `content` (string or OpenAI part list) as Anthropic blocks."""
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content.strip() else []
    blocks: list[dict] = []
    for p in content if isinstance(content, list) else [content]:
        if isinstance(p, str):
            if p.strip():
                blocks.append({"type": "text", "text": p})
        elif isinstance(p, dict):
            kind = p.get("type")
            if kind == "text" and str(p.get("text", "")).strip():
                blocks.append({"type": "text", "text": p["text"]})
            elif kind == "image_url":
                url = (p.get("image_url") or {}).get("url", "")
                img = _image_block(url)
                if img:
                    blocks.append(img)
            elif kind in ("image", "tool_use", "tool_result"):
                blocks.append(p)            # already Anthropic-shaped
    return blocks


def _tool_result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except Exception:
        return str(content)


def to_anthropic_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    """(system text, Anthropic messages) from an OpenAI-style history.

    Guarantees the API's structural rules hold whatever the history looks like
    after trimming: roles alternate, the first turn is the user's, and every
    tool_result answers a tool_use in the turn right before it (an orphan is
    turned into plain text instead of failing the request with a 400).
    """
    system_parts: list[str] = []
    out: list[dict] = []

    def push(role: str, blocks: list[dict]) -> None:
        if not blocks:
            return
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": list(blocks)})

    for m in messages or []:
        role = m.get("role")
        if role == "system":
            text = m.get("content")
            if isinstance(text, list):
                text = " ".join(b.get("text", "") for b in text if isinstance(b, dict))
            if text:
                system_parts.append(str(text))
        elif role == "user":
            push("user", _content_blocks(m.get("content")))
        elif role == "assistant":
            blocks = _content_blocks(m.get("content"))
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                raw = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw) if isinstance(raw, str) else dict(raw)
                except Exception:
                    args = {}
                blocks.append({"type": "tool_use",
                               "id": tc.get("id") or f"toolu_{len(out)}",
                               "name": fn.get("name") or tc.get("name") or "tool",
                               "input": args if isinstance(args, dict) else {"value": args}})
            push("assistant", blocks)
        elif role == "tool":
            push("user", [{"type": "tool_result",
                           "tool_use_id": m.get("tool_call_id") or "",
                           "content": _tool_result_text(m.get("content"))}])

    # Orphaned tool results (their tool_use was trimmed away) become text.
    for i, msg in enumerate(out):
        if msg["role"] != "user":
            continue
        prev_ids = set()
        if i > 0 and out[i - 1]["role"] == "assistant":
            prev_ids = {b.get("id") for b in out[i - 1]["content"]
                        if b.get("type") == "tool_use"}
        fixed = []
        for b in msg["content"]:
            if b.get("type") == "tool_result" and b.get("tool_use_id") not in prev_ids:
                fixed.append({"type": "text",
                              "text": f"[earlier tool result] {b.get('content', '')}"})
            else:
                fixed.append(b)
        # tool_result blocks must come first in a user turn.
        fixed.sort(key=lambda b: 0 if b.get("type") == "tool_result" else 1)
        msg["content"] = fixed

    # Likewise an assistant tool_use with no answer after it would 400.
    for i, msg in enumerate(out):
        if msg["role"] != "assistant":
            continue
        uses = [b.get("id") for b in msg["content"] if b.get("type") == "tool_use"]
        if not uses:
            continue
        nxt = out[i + 1] if i + 1 < len(out) else None
        answered = {b.get("tool_use_id") for b in (nxt or {}).get("content", [])
                    if b.get("type") == "tool_result"} if nxt and nxt["role"] == "user" else set()
        if set(uses) - answered:
            msg["content"] = [b if b.get("type") != "tool_use" or b.get("id") in answered
                              else {"type": "text",
                                    "text": f"[called {b.get('name')}]"}
                              for b in msg["content"]]

    if not out or out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": [{"type": "text", "text": "(continue)"}]})
    return "\n\n".join(system_parts), out


def to_anthropic_tools(tools: list[dict] | None) -> list[dict]:
    """Accept tools in either shape; return Anthropic's."""
    out = []
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        if "input_schema" in t:
            out.append({k: t[k] for k in ("name", "description", "input_schema") if k in t})
        elif t.get("type") == "function" and isinstance(t.get("function"), dict):
            fn = t["function"]
            out.append({"name": fn.get("name"), "description": fn.get("description", ""),
                        "input_schema": fn.get("parameters") or {"type": "object", "properties": {}}})
    return [t for t in out if t.get("name")]


# ─────────────────────────────────────────────────────────────────────────────
#  The provider
# ─────────────────────────────────────────────────────────────────────────────
class AnthropicLLM:
    def __init__(self, cfg: dict):
        import httpx  # inside the factory path — never at module scope

        self._httpx = httpx
        self.base = (cfg.get("base_url") or API_URL).rstrip("/")
        self.model = (cfg.get("model") or DEFAULT_MODEL).strip()
        self.key = (cfg.get("api_key") or "").strip()
        self.timeout = float(cfg.get("timeout") or 90.0)
        try:
            self.max_tokens = int(cfg.get("max_tokens") or 4096)
        except (TypeError, ValueError):
            self.max_tokens = 4096
        self.cache = str(cfg.get("prompt_cache", "1")).strip().lower() not in ("", "0", "false", "no")
        self.routing = str(cfg.get("routing", "1")).strip().lower() not in ("", "0", "false", "no")
        self.fast_model = (cfg.get("fast_model") or FAST_MODEL).strip()
        self._turn_model = self.model

        self.caps = dict(DEFAULT_CAPS)
        self.caps.update({
            "tools": True,
            "tool_format": "anthropic",
            "vision": True,          # every current Claude model reads images
            "max_tools": None,
            "max_tool_chars": None,
        })

    # -- request building ---------------------------------------------------
    def _headers(self) -> dict:
        return {"x-api-key": self.key, "anthropic-version": API_VERSION,
                "content-type": "application/json"}

    def _body(self, messages: list[dict], tools: list[dict] | None,
              stream: bool, temperature: float | None,
              max_tokens: int | None = None, model: str | None = None) -> dict:
        system, msgs = to_anthropic_messages(messages)
        body: dict[str, Any] = {"model": model or self.model,
                                "max_tokens": int(max_tokens or self.max_tokens),
                                "messages": msgs}
        if stream:
            body["stream"] = True
        if system:
            block = {"type": "text", "text": system}
            if self.cache:
                block["cache_control"] = {"type": "ephemeral"}
            body["system"] = [block]
        ant_tools = to_anthropic_tools(tools)
        if ant_tools:
            if self.cache:
                ant_tools[-1] = dict(ant_tools[-1], cache_control={"type": "ephemeral"})
            body["tools"] = ant_tools
        if temperature is not None:
            body["temperature"] = max(0.0, min(1.0, float(temperature)))
        return body

    @staticmethod
    def _parse_message(data: dict) -> tuple[str, list[FunctionCall]]:
        text, calls = [], []
        for b in data.get("content") or []:
            if b.get("type") == "text":
                text.append(b.get("text", ""))
            elif b.get("type") == "tool_use":
                calls.append(FunctionCall(id=b.get("id", ""), name=b.get("name", ""),
                                          args=b.get("input") or {}))
        return "".join(text), calls

    # -- one-shot, synchronous: core/gemini.py's ladder uses this -----------
    def complete(self, messages: list[dict], tools: list[dict] | None = None,
                 *, timeout: float | None = None,
                 temperature: float | None = None,
                 max_tokens: int | None = None) -> tuple[str, list[FunctionCall]]:
        if not self.key:
            raise RuntimeError("401 no Anthropic API key configured")
        with self._httpx.Client(timeout=timeout or self.timeout) as cl:
            r = cl.post(f"{self.base}/messages", headers=self._headers(),
                        json=self._body(messages, tools, False, temperature, max_tokens))
            if r.status_code >= 400:
                raise RuntimeError(f"{r.status_code} {_terse(r.text)}")
            return self._parse_message(r.json())

    # -- streaming, async: the pipeline engine uses this ---------------------
    async def chat(self, messages: list[dict], tools: list[dict] | None = None,
                   *, stream: bool = True,
                   temperature: float | None = None) -> AsyncIterator[dict]:
        """Yields {"text": ...} as it arrives, then one {"tool_calls": [...]}."""
        if not self.key:
            raise RuntimeError("401 no Anthropic API key configured")
        model = self.model
        if self.routing and self.fast_model and self.fast_model != self.model:
            choice = route(messages)
            if choice:
                self._turn_model = self.fast_model if choice == "fast" else self.model
            model = self._turn_model
        self.last_model = model
        body = self._body(messages, tools, stream, temperature, model=model)
        async with self._httpx.AsyncClient(timeout=self.timeout) as cl:
            if not stream:
                r = await cl.post(f"{self.base}/messages", headers=self._headers(), json=body)
                if r.status_code >= 400:
                    raise RuntimeError(f"{r.status_code} {_terse(r.text)}")
                text, calls = self._parse_message(r.json())
                if text:
                    yield {"text": text}
                if calls:
                    yield {"tool_calls": calls}
                return

            blocks: dict[int, dict] = {}
            async with cl.stream("POST", f"{self.base}/messages",
                                 headers=self._headers(), json=body) as r:
                if r.status_code >= 400:
                    detail = _terse((await r.aread()).decode("utf-8", "replace"))
                    raise RuntimeError(f"{r.status_code} {detail}")
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        ev = json.loads(line[5:].strip())
                    except Exception:
                        continue
                    for out in self.feed_event(ev, blocks):
                        yield out
            calls = self.drain_calls(blocks)
            if calls:
                yield {"tool_calls": calls}

    @staticmethod
    def feed_event(ev: dict, blocks: dict[int, dict]) -> list[dict]:
        """One SSE event -> zero or more {"text": ...} items. Tool calls are
        accumulated in `blocks` and drained once the stream ends."""
        kind = ev.get("type")
        if kind == "error":
            err = ev.get("error") or {}
            raise RuntimeError(f"{err.get('type', 'error')}: {err.get('message', '')}")
        if kind == "content_block_start":
            cb = ev.get("content_block") or {}
            blocks[ev.get("index", 0)] = {"type": cb.get("type"), "id": cb.get("id", ""),
                                          "name": cb.get("name", ""), "json": ""}
            if cb.get("type") == "text" and cb.get("text"):
                return [{"text": cb["text"]}]
        elif kind == "content_block_delta":
            d = ev.get("delta") or {}
            if d.get("type") == "text_delta" and d.get("text"):
                return [{"text": d["text"]}]
            if d.get("type") == "input_json_delta":
                slot = blocks.setdefault(ev.get("index", 0), {"type": "tool_use", "id": "",
                                                              "name": "", "json": ""})
                slot["json"] += d.get("partial_json", "")
        return []

    @staticmethod
    def drain_calls(blocks: dict[int, dict]) -> list[FunctionCall]:
        calls = []
        for idx in sorted(blocks):
            b = blocks[idx]
            if b.get("type") != "tool_use" or not b.get("name"):
                continue
            try:
                args = json.loads(b["json"]) if b["json"].strip() else {}
            except Exception:
                args = {}
            calls.append(FunctionCall(id=b.get("id") or f"toolu_{idx}",
                                      name=b["name"], args=args if isinstance(args, dict) else {}))
        blocks.clear()
        return calls

    # -- for the settings panel ---------------------------------------------
    def models(self, timeout: float = 8.0) -> list[str]:
        with self._httpx.Client(timeout=timeout) as cl:
            r = cl.get(f"{self.base}/models", headers=self._headers())
            r.raise_for_status()
            rows = r.json().get("data") or []
        found = sorted({str(m.get("id")) for m in rows if m.get("id")})
        return found or list(MODELS)

    def ensure_vision_known(self, save=None) -> bool:
        return True

    def probe(self, check_vision: bool = True) -> tuple[bool, str]:
        if not self.key:
            return False, "Paste your Anthropic API key (console.anthropic.com)."
        t0 = time.monotonic()
        try:
            self.complete([{"role": "user", "content": "Reply with the single word: ok"}],
                          timeout=20.0, max_tokens=8)
        except Exception as e:
            msg = str(e)
            if "401" in msg or "403" in msg:
                return False, "Anthropic refused the key."
            if "404" in msg:
                return False, f"No model called '{self.model}'."
            if "429" in msg:
                return False, "Rate limited or out of credit."
            return False, _terse(msg, 90)
        return True, f"Claude answered in {time.monotonic() - t0:.2f}s  |  can see images"


def _terse(text: str, limit: int = 160) -> str:
    text = " ".join((text or "").split())
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            err = obj.get("error", obj)
            text = str(err.get("message") if isinstance(err, dict) else err)
    except Exception:
        pass
    return text[:limit] + ("..." if len(text) > limit else "")
