"""Text conversation with Claude and every tool — for agents without Gemini
Live (a quick text box, a watch, a script) and as a fallback when Live is down."""
from __future__ import annotations

import json

from . import store, tools
from .hub import hub
from .prompt import build

MAX_ROUNDS = 8
SELF = "this_device__"


VOICE_NOTE = ("This reply will be SPOKEN ALOUD by a small room speaker: plain sentences only - no markdown, "
              "lists, links or emoji. Keep it to one to three short sentences unless they ask for detail.")


def room_device(device: dict, room: str = "", satellite: str = "") -> dict:
    """A room speaker behind a hub (Home Assistant): its own name and conversation history."""
    label = (room or satellite or "room").strip()
    slug = "".join(ch for ch in label.lower() if ch.isalnum() or ch in "-_ ").strip().replace(" ", "-")[:40]
    return {"id": f"{device['id']}~{slug or 'room'}", "name": satellite or f"{label} speaker",
            "kind": "room", "room": room, "hub_id": device["id"]}


def reply(device: dict, text: str, voice: bool = False) -> dict:
    from .claude import route
    from .tools.brain import claude
    store.add_turn(device["id"], "user", text)
    decls = tools.declarations("chat")
    own = hub.conns[device["id"]].capabilities if hub.is_online(device["id"]) else []
    decls += [{"name": SELF + c["name"], "description": "[on THIS device] " + c.get("description", ""),
               "parameters": c.get("parameters") or {"type": "OBJECT", "properties": {}}} for c in own]
    system = build(device)
    if device.get("room"):
        r = device["room"]
        system += (f"\n\n[WHERE YOU ARE]\nThe person is talking to you from the {r}. 'Here', 'in here', 'this room' "
                   f"and things like 'the lights' or 'the fan' with no room named mean the {r} - pass "
                   f"'{r}' as the area/room when you use the smart home.")
    system += "\n\n" + (VOICE_NOTE if voice else "This is a TEXT chat; markdown is fine.")
    history = [{"role": "system", "content": system}]
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
                result = tools.run(c.name, c.args, tools.Ctx(device["id"], device["name"], "chat",
                                                             {"room": device.get("room", "")}))
            history.append({"role": "tool", "tool_call_id": c.id, "content": result[:6000]})
    else:
        answer = "I got stuck in a loop of tool calls — try rephrasing."
    store.add_turn(device["id"], "assistant", answer)
    return {"text": answer, "tools": used}
