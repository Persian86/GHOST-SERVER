"""
The server's tool registry.

Declarations are written in Gemini's function-declaration dialect (types in
UPPER CASE) because that is what every agent hands straight to Gemini Live.
`to_claude()` converts them for Claude, which wants JSON Schema.

A handler is a plain function `handler(args: dict, ctx: Ctx) -> str`. It runs
on a worker thread, so it may block (HTTP calls, file building). It never
raises to the caller: the dispatcher turns exceptions into a spoken error.
"""
from __future__ import annotations

import importlib
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

TOOLS: dict[str, "Tool"] = {}
_MODULES = ("brain", "memory", "home", "marquee", "web", "documents", "devices", "tasks", "security")
_loaded = False


@dataclass
class Ctx:
    device_id: str = ""
    device_name: str = ""
    via: str = "live"            # live | chat | task
    extra: dict = field(default_factory=dict)


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Callable[[dict, Ctx], str]
    # "live" tools are offered to agents' Gemini sessions; "internal" ones only
    # to the server's own Claude loops.
    expose: tuple = ("live", "chat", "task")


def tool(name: str, description: str, parameters: dict | None = None, expose=("live", "chat", "task")):
    def deco(fn):
        TOOLS[name] = Tool(name, " ".join(description.split()), parameters or {"type": "OBJECT", "properties": {}},
                           fn, tuple(expose))
        return fn
    return deco


def load() -> None:
    global _loaded
    if _loaded:
        return
    for m in _MODULES:
        importlib.import_module(f"{__name__}.{m}")
    _loaded = True


def declarations(via: str = "live", exclude: set | None = None) -> list[dict]:
    load()
    exclude = exclude or set()
    return [{"name": t.name, "description": t.description, "parameters": t.parameters}
            for t in TOOLS.values() if via in t.expose and t.name not in exclude]


def _json_schema(node: Any) -> Any:
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k == "type" and isinstance(v, str):
                out[k] = v.lower()
            else:
                out[k] = _json_schema(v)
        return out
    if isinstance(node, list):
        return [_json_schema(v) for v in node]
    return node


def to_claude(decls: list[dict]) -> list[dict]:
    return [{"name": d["name"], "description": d["description"],
             "input_schema": _json_schema(d.get("parameters") or {"type": "OBJECT", "properties": {}})}
            for d in decls]


def run(name: str, args: dict | None, ctx: Ctx) -> str:
    load()
    t = TOOLS.get(name)
    if t is None:
        return f"Unknown tool '{name}'."
    try:
        out = t.handler(dict(args or {}), ctx)
        return out if isinstance(out, str) else str(out)
    except Exception as e:
        traceback.print_exc()
        return f"The {name} tool failed: {type(e).__name__}: {e}"
