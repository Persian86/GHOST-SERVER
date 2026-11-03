"""Text conversation with Claude and every tool — for agents without Gemini
Live (a quick text box, a watch, a script) and as a fallback when Live is down."""
from __future__ import annotations

import json

from . import store, tools
from .hub import hub
from .prompt import build

MAX_ROUNDS = 8
SELF = "this_device__"


def reply(device: dict, text: str) -> dict:
    from .claude import route
    from .tools.brain import claude
    store.add_turn(device["id"], "user", text)
    decls = tools.declarations("chat")
    own = hub.conns[device["id"]].capabilities if hub.is_online(device["id"]) else []
    decls += [{"name": SELF + c["name"], "description": "[on THIS device] " + c.get("description", ""),
               "parameters": c.get("parameters") or {"type": "OBJECT", "properties": {}}} for c in own]
    history = [{"role": "system", "content": build(device) + "\n\nThis is a TEXT chat; markdown is fine."}]
    for t in store.recent_turns(12, device["id"])[:-1]:
        history.append({"role": "assistant" if t["role"] == "assistant" else "user", "content": t["text"]})
    history.append({"role": "user", "content": text})
    llm = claude(fast=route(history) == "fast")
    used = []
    for _ in range(MAX_ROUNDS):
        answer, calls = llm.complete(history, tools=tools.to_claude(decls), timeout=120, max_tokens=3000)
        if not calls:
            break
        history.append({"role": "assistant", "content": answer or None, "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.args)}}
            for c in calls]})
        for c in calls:
            used.append(c.name)
            if c.name.startswith(SELF):
                r = hub.call_sync(device["id"], c.name[len(SELF):], c.args)
                result = str(r.get("result")) if r.get("ok") else f"failed: {r.get('error')}"
            else:
                result = tools.run(c.name, c.args, tools.Ctx(device["id"], device["name"], "chat"))
            history.append({"role": "tool", "tool_call_id": c.id, "content": result[:6000]})
    else:
        answer = "I got stuck in a loop of tool calls — try rephrasing."
    store.add_turn(device["id"], "assistant", answer)
    return {"text": answer, "tools": used}
