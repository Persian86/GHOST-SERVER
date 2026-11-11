"""A deterministic wrapper for explicit, owner-configured routines."""
import json
from . import Ctx,tool
from .. import routines


@tool("routines","""List or explicitly start an owner-configured routine by its exact name.
Only start when the user asks. Use status to check progress. If waiting for owner approval,
ask the user to review Ghost Server → Security, then resume only when they ask.
Cancel stops later steps, not an action already in progress. Never start a fresh run to
silently retry failed/interrupted work. This is not a scheduler or location trigger.""",
      {"type":"OBJECT","properties":{
          "action":{"type":"STRING","description":"list | start | status | resume | cancel"},
          "name":{"type":"STRING","description":"Exact saved routine name for start"},
          "run_id":{"type":"STRING","description":"Run identifier for status/resume/cancel"}},"required":["action"]},
      expose=("live","chat"))
def run_routine(args:dict,ctx:Ctx):
    if ctx.via not in ("live","chat"):return "Routines require an explicit live/chat request, not a nested task or automation."
    try:
        action=args.get("action")
        if action=="list":
            return json.dumps([{"name":r["name"],"steps":len(r["steps"])} for r in routines.definitions(ctx.device_id)])
        if action=="start":run=routines.start(str(args.get("name") or ""),ctx.device_id)
        elif action=="status":
            records=[routines.get_run(str(args["run_id"]),ctx.device_id)] if args.get("run_id") else routines.runs(ctx.device_id)
            return json.dumps([{"id":r["id"],"name":r["name"],"status":r["status"],"next_step":r["next_step"],"results":r["results"]} for r in records[:5]])
        elif action=="resume":run=routines.resume(str(args.get("run_id") or ""),ctx.device_id)
        elif action=="cancel":run=routines.cancel(str(args.get("run_id") or ""),ctx.device_id)
        else:return "Choose list, start, status, resume or cancel."
        return json.dumps({"id":run["id"],"name":run["name"],"status":run["status"],"next_step":run["next_step"]})
    except (ValueError,PermissionError) as exc:return str(exc)
