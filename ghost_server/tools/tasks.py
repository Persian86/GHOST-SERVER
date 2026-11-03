"""Multi-step jobs, run by Claude on the server with every server tool AND every
online device's capabilities. The device that asked is told when it's done."""
from __future__ import annotations

import json
import secrets
import threading
import time

from . import Ctx, tool
from .. import store

MAX_STEPS = 15
TASKS: dict[str, dict] = {}

_SYSTEM = ("You are the task engine of Ghost, a household assistant running on a home server. Complete the "
           "goal step by step with the tools given. Use device_action to act on the user's devices. Plan "
           "briefly, prefer doing over describing, never invent results. Finish with a 2-4 sentence spoken "
           "summary of what was done (include download links for files).")


def _work(task_id: str, goal: str, ctx: Ctx) -> None:
    from . import declarations, run, to_claude
    from .brain import claude
    from ..hub import hub
    t = TASKS[task_id]
    try:
        llm = claude()
        tools = to_claude(declarations("task", exclude={"run_task"}))
        history = [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": goal}]
        final = ""
        for step in range(MAX_STEPS):
            text, calls = llm.complete(history, tools=tools, timeout=180, max_tokens=4000)
            if not calls:
                final = text.strip()
                break
            history.append({"role": "assistant", "content": text or None, "tool_calls": [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.args)}}
                for c in calls]})
            for c in calls:
                t["log"].append(f"{c.name} {json.dumps(c.args)[:100]}")
                result = run(c.name, c.args, Ctx(ctx.device_id, ctx.device_name, "task"))
                history.append({"role": "tool", "tool_call_id": c.id, "content": result[:6000]})
        else:
            history.append({"role": "user", "content": "Stop. Summarise what was finished and what is left."})
            final, _ = llm.complete(history, timeout=120, max_tokens=800)
        t.update(status="done", result=final or "Finished.")
    except Exception as e:
        t.update(status="failed", result=str(e))
    store.add_event(ctx.device_id, "task", {"goal": goal, "status": t["status"], "result": t["result"][:500]})
    hub.send_sync(ctx.device_id, {"type": "task_done", "task": task_id, "status": t["status"],
                                  "text": t["result"], "say": f"[TASK {t['status'].upper()}] {t['result']}"})


@tool("run_task", """Run a MULTI-STEP goal in the background on the home server — e.g. 'research NAS drives
and make a deck', 'check the house then send a summary to my phone'. Pass the whole goal. Only for
requests that need several tools in sequence. You'll be told when it finishes. action 'status' lists tasks.""",
      {"type": "OBJECT", "properties": {
          "action": {"type": "STRING", "description": "start (default) | status"},
          "goal": {"type": "STRING"}}})
def run_task(args: dict, ctx: Ctx) -> str:
    if str(args.get("action", "start")).lower() == "status":
        return "; ".join(f"{k}: {v['goal'][:50]} — {v['status']} ({len(v['log'])} steps)"
                         for k, v in list(TASKS.items())[-5:]) or "No tasks yet."
    goal = str(args.get("goal") or "").strip()
    if not goal:
        return "Describe the whole goal."
    if sum(v["status"] == "running" for v in TASKS.values()) >= 3:
        return "Three tasks are already running — wait for one to finish."
    tid = "t_" + secrets.token_hex(3)
    TASKS[tid] = {"goal": goal, "status": "running", "log": [], "result": "", "device": ctx.device_id,
                  "started": time.time()}
    threading.Thread(target=_work, args=(tid, goal, ctx), daemon=True).start()
    return f"Started task {tid} on the server. Tell the user briefly; you'll get a message when it's done."
