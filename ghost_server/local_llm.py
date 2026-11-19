"""
Offline backup brain: a local model (Ollama on the AI PC) that takes over when
Claude can't be reached — the internet is down, Anthropic is having an outage,
or the credit has run out.

HOW IT FITS
    Everything in Ghost Server gets its brain from `tools.brain.claude()`, and
    every caller uses the same `complete(messages, tools=...)` contract. That
    factory now returns a `ResilientLLM`: it asks Claude first and, if Claude
    can't answer, asks the local model with the very same history and tools.
    So chat, Home Assistant voice, routines, tasks, meals, door/camera notes
    all keep working offline — just a bit less clever.

    After Claude fails, the next couple of minutes go straight to the local
    model (no 20-second wait for a dead connection on every request); then
    Claude is tried again automatically.

MODES (admin page → "Local AI — offline backup")
    off       Claude only (the old behaviour).
    fallback  Claude first, local model when Claude can't be reached. Default.
    only      local model for everything (fully offline, no cloud at all).

WHY OLLAMA'S NATIVE /api/chat
    It accepts `think: false`, which keeps a thinking model (Qwen 3.5) quick
    for voice, and returns tool arguments as JSON objects. The history Ghost
    keeps is OpenAI-shaped (tool_calls / tool_call_id), so it is translated
    here at the edge, exactly as claude.py does for Anthropic.
"""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Any

from .claude import FunctionCall

DEFAULT_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3.5:9b"
# How long to skip Claude after it failed to answer, before trying it again.
OFFLINE_COOLDOWN_S = 120.0

_THINK = re.compile(r"<think>.*?</think>", re.S)


# ─────────────────────────────────────────────────────────────────────────────
#  Translation: OpenAI-shaped history / Claude-shaped tools  →  Ollama
# ─────────────────────────────────────────────────────────────────────────────
def to_ollama_tools(tools: list[dict] | None) -> list[dict]:
    """Accept tools in Claude's shape ({name, description, input_schema}) or
    OpenAI's ({type: function, function: {...}}); return Ollama's."""
    out = []
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        if t.get("type") == "function" and isinstance(t.get("function"), dict):
            fn = t["function"]
            name, desc, params = fn.get("name"), fn.get("description", ""), fn.get("parameters")
        else:
            name, desc, params = t.get("name"), t.get("description", ""), t.get("input_schema")
        if not name:
            continue
        out.append({"type": "function", "function": {
            "name": name, "description": desc or "",
            "parameters": params or {"type": "object", "properties": {}}}})
    return out


def _split_content(content: Any) -> tuple[str, list[str]]:
    """Text and base64 images from a message's content (a string, or a list of
    OpenAI parts / Anthropic blocks)."""
    if content is None:
        return "", []
    if isinstance(content, str):
        return content, []
    text, images = [], []
    for part in content if isinstance(content, list) else [content]:
        if isinstance(part, str):
            text.append(part)
            continue
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        if kind == "text":
            text.append(part.get("text", ""))
        elif kind == "image_url":
            url = (part.get("image_url") or {}).get("url", "") if isinstance(part.get("image_url"), dict) \
                else str(part.get("image_url") or "")
            if url.startswith("data:") and "," in url:
                images.append(url.split(",", 1)[1])
        elif kind == "image":
            src = part.get("source") or {}
            if src.get("type") == "base64" and src.get("data"):
                images.append(src["data"])
    return "\n".join(t for t in text if t), images


def to_ollama_messages(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    names: dict[str, str] = {}          # tool_call_id → tool name, for the tool results
    for m in messages or []:
        role = m.get("role")
        text, images = _split_content(m.get("content"))
        if role in ("system", "user"):
            msg: dict[str, Any] = {"role": role, "content": text}
            if images:
                msg["images"] = images
            out.append(msg)
        elif role == "assistant":
            msg = {"role": "assistant", "content": text}
            calls = []
            for c in m.get("tool_calls") or []:
                fn = c.get("function") or {}
                args = fn.get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args) if args.strip() else {}
                    except Exception:
                        args = {}
                names[c.get("id", "")] = fn.get("name", "")
                calls.append({"function": {"name": fn.get("name", ""), "arguments": args or {}}})
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        elif role == "tool":
            msg = {"role": "tool", "content": text}
            name = names.get(m.get("tool_call_id", ""))
            if name:
                msg["tool_name"] = name
            out.append(msg)
    return out


# ─────────────────────────────────────────────────────────────────────────────
#  The local model
# ─────────────────────────────────────────────────────────────────────────────
class OllamaLLM:
    """Same `complete()` contract as AnthropicLLM: returns (text, [FunctionCall])."""

    def __init__(self, cfg: dict, transport=None):
        import httpx
        self._httpx = httpx
        self._transport = transport          # tests inject a fake server here
        self.base = (cfg.get("url") or DEFAULT_URL).strip().rstrip("/")
        if self.base and not re.match(r"^https?://", self.base):
            self.base = "http://" + self.base
        self.model = (cfg.get("model") or DEFAULT_MODEL).strip()
        self.think = bool(cfg.get("think", False))
        self.num_ctx = int(cfg.get("num_ctx") or 16384)

    def _client(self, timeout: float):
        kw = {"timeout": timeout}
        if self._transport is not None:
            kw["transport"] = self._transport
        return self._httpx.Client(**kw)

    def complete(self, messages: list[dict], tools: list[dict] | None = None,
                 *, timeout: float | None = None,
                 temperature: float | None = None,
                 max_tokens: int | None = None) -> tuple[str, list[FunctionCall]]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": to_ollama_messages(messages),
            "stream": False,
            "think": self.think,
            "keep_alive": "30m",
            "options": {"num_ctx": self.num_ctx},
        }
        if max_tokens:
            body["options"]["num_predict"] = int(max_tokens)
        if temperature is not None:
            body["options"]["temperature"] = float(temperature)
        tl = to_ollama_tools(tools)
        if tl:
            body["tools"] = tl
        with self._client(timeout or 120.0) as cl:
            r = cl.post(f"{self.base}/api/chat", json=body)
            if r.status_code >= 400:
                raise RuntimeError(f"local model {r.status_code}: {r.text[:160]}")
            msg = (r.json() or {}).get("message") or {}
        text = _THINK.sub("", msg.get("content") or "").strip()
        calls = []
        for i, c in enumerate(msg.get("tool_calls") or []):
            fn = c.get("function") or {}
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            if fn.get("name"):
                calls.append(FunctionCall(id=c.get("id") or f"local_{int(time.time() * 1000)}_{i}",
                                          name=fn["name"], args=args if isinstance(args, dict) else {}))
        return text, calls

    def installed(self, timeout: float = 5.0) -> list[str]:
        with self._client(timeout) as cl:
            r = cl.get(f"{self.base}/api/tags")
            r.raise_for_status()
            return [m.get("name", "") for m in (r.json() or {}).get("models") or []]

    def probe(self) -> tuple[bool, str]:
        try:
            have = self.installed()
        except Exception as e:
            return False, f"Can't reach Ollama at {self.base} ({type(e).__name__})."
        if not any(h == self.model or h.split(":")[0] == self.model for h in have):
            return False, (f"Ollama is running but '{self.model}' isn't downloaded. "
                           f"Run: ollama pull {self.model}")
        t0 = time.monotonic()
        try:
            text, _ = self.complete([{"role": "user", "content": "Reply with the single word: ok"}],
                                    timeout=120, max_tokens=8)
        except Exception as e:
            return False, str(e)[:160]
        return True, f"{self.model} answered “{text[:30]}” in {time.monotonic() - t0:.1f}s"

    # AnthropicLLM's other methods, so callers never need to care which brain this is.
    def ensure_vision_known(self, save=None) -> bool:
        return True


# ─────────────────────────────────────────────────────────────────────────────
#  Claude first, local when Claude can't answer
# ─────────────────────────────────────────────────────────────────────────────
_state = {"offline_until": 0.0, "last_error": "", "last_used": ""}
_lock = threading.Lock()


def status() -> dict:
    with _lock:
        left = max(0.0, _state["offline_until"] - time.monotonic())
        return {"claude_unreachable": left > 0, "retry_in_s": round(left),
                "last_error": _state["last_error"], "last_brain": _state["last_used"]}


def _mark(brain: str, error: str = "", offline: bool = False) -> None:
    with _lock:
        _state["last_used"] = brain
        if error:
            _state["last_error"] = error[:200]
        if offline:
            _state["offline_until"] = time.monotonic() + OFFLINE_COOLDOWN_S
        elif brain == "claude":
            _state["offline_until"] = 0.0


def should_fall_back(err: Exception) -> bool:
    """Claude couldn't answer for a reason the local model can fix: no network,
    timeouts, Anthropic overloaded/down, out of credit, no key. NOT for a
    request Claude rejected as malformed — the local model would fail too and
    hide a real bug."""
    msg = str(err)
    if re.match(r"^\s*(400|404|413|422)\b", msg):
        return False
    return True


class ResilientLLM:
    def __init__(self, primary, local: OllamaLLM | None, mode: str = "fallback"):
        self.primary = primary          # an AnthropicLLM, or None when there's no key
        self.local = local
        self.mode = mode if mode in ("off", "fallback", "only") else "fallback"
        self.last_brain = ""

    def __getattr__(self, name):        # .model, .caps, … pass through to Claude
        target = self.primary or self.local
        if target is None:
            raise AttributeError(name)
        return getattr(target, name)

    def _local(self, messages, tools, timeout, temperature, max_tokens, why: str):
        if self.local is None:
            raise RuntimeError(why or "No brain is available")
        text, calls = self.local.complete(messages, tools, timeout=timeout,
                                          temperature=temperature, max_tokens=max_tokens)
        self.last_brain = "local"
        _mark("local")
        return text, calls

    def complete(self, messages: list[dict], tools: list[dict] | None = None,
                 *, timeout: float | None = None,
                 temperature: float | None = None,
                 max_tokens: int | None = None) -> tuple[str, list[FunctionCall]]:
        if self.mode == "only" or self.primary is None:
            if self.local is None or self.mode == "off":
                raise RuntimeError("Claude isn't set up — add the Anthropic key on the Ghost Server admin page")
            return self._local(messages, tools, timeout, temperature, max_tokens, "")
        if self.mode == "fallback" and self.local is not None and status()["claude_unreachable"]:
            return self._local(messages, tools, timeout, temperature, max_tokens, "")
        try:
            # Claude gets a shorter leash when there's a backup waiting.
            t = timeout if self.local is None or self.mode == "off" else min(timeout or 90.0, 45.0)
            text, calls = self.primary.complete(messages, tools, timeout=t,
                                                temperature=temperature, max_tokens=max_tokens)
            self.last_brain = "claude"
            _mark("claude")
            return text, calls
        except Exception as e:
            if self.mode == "off" or self.local is None or not should_fall_back(e):
                raise
            print(f"[brain] Claude unavailable ({str(e)[:120]}) — answering with the local model")
            _mark("local", error=str(e), offline=True)
            try:
                return self._local(messages, tools, timeout, temperature, max_tokens, str(e))
            except Exception as le:
                raise RuntimeError(f"Claude failed ({str(e)[:100]}) and so did the local model ({str(le)[:100]})") from e


def local_from_settings(store) -> OllamaLLM | None:
    if (store.get("local_llm_mode") or "fallback") == "off":
        return None
    url = (store.get("local_llm_url") or "").strip()
    if not url:
        return None
    return OllamaLLM({"url": url, "model": store.get("local_llm_model") or DEFAULT_MODEL,
                      "think": bool(store.get("local_llm_think"))})
