"""Owner-defined, checkpointed routines. Explicit start/resume only; never replay an uncertain step."""
from __future__ import annotations
import json
import re
import secrets
import threading
import time
from . import access,store

KINDS={"briefing","weather","timer","home","message"}
ACTIVE={"running","cancelling","waiting_approval","interrupted"}
SCHEMA="""CREATE TABLE IF NOT EXISTS routines(
 id TEXT PRIMARY KEY,name TEXT UNIQUE COLLATE NOCASE,enabled INTEGER,version INTEGER,
 device_id TEXT,steps TEXT,updated REAL);
CREATE TABLE IF NOT EXISTS routine_runs(
 id TEXT PRIMARY KEY,routine_id TEXT,name TEXT,version INTEGER,device_id TEXT,steps TEXT,
 next_step INTEGER,status TEXT,results TEXT,created REAL,updated REAL,
 inflight INTEGER DEFAULT 0,cancel_requested INTEGER DEFAULT 0,attempt INTEGER DEFAULT 1);
"""
_lock=threading.RLock()


def schema():store._con().executescript(SCHEMA)
def _text(v,limit,label):
    if not isinstance(v,str) or len(v.strip())>limit or any(ord(c)<32 for c in v):raise ValueError(f"{label} must be one line, at most {limit} characters")
    return v.strip()


def validate_steps(steps):
    if not isinstance(steps,list) or not 1<=len(steps)<=8:raise ValueError("Use one to eight steps")
    out=[]
    for s in steps:
        if not isinstance(s,dict) or not isinstance(s.get("kind"),str) or s.get("kind") not in KINDS:raise ValueError("Choose a supported routine step")
        kind=s["kind"];allowed={"kind"};value={"kind":kind}
        if kind=="weather":
            allowed|={"city"};value["city"]=_text(s.get("city",""),80,"City")
        elif kind=="timer":
            allowed|={"seconds","label"};seconds=s.get("seconds")
            if type(seconds) is not int or not 1<=seconds<=86400:raise ValueError("Timer must be 1 second to 24 hours")
            value.update(seconds=seconds,label=_text(s.get("label",""),40,"Timer label"))
        elif kind=="home":
            allowed|={"entity_id","action"};eid=_text(s.get("entity_id",""),100,"Entity ID")
            if not re.fullmatch(r"(?:light|fan|media_player)\.[A-Za-z0-9_]+",eid):raise ValueError("Home steps support an exact light, fan or media-player entity ID")
            if s.get("action") not in ("on","off","toggle"):raise ValueError("Choose on, off or toggle")
            value.update(entity_id=eid,action=s["action"])
        elif kind=="message":
            allowed|={"text"};value["text"]=_text(s.get("text",""),300,"Reply message")
            if not value["text"]:raise ValueError("Enter a reply message")
        if set(s)-allowed:raise ValueError("Unknown routine step field")
        out.append(value)
    return out


def _device(did):
    if not isinstance(did,str) or not did:raise ValueError("Choose an approved paired device")
    d=store.device(did)
    if d is None or d["revoked"] or not d["approved"] or "~" in did:raise ValueError("Choose an approved paired device")
    if not access.tool_allowed(did,"routines"):raise PermissionError("This device's access profile cannot run routines")
    return dict(d)


def _definition(rid):
    schema();rows=store.q("SELECT * FROM routines WHERE id=?",(rid,))
    if not rows:rows=store.q("SELECT * FROM routines WHERE name=? COLLATE NOCASE",(rid,))
    if not rows:raise ValueError("No matching routine")
    return dict(rows[0])


def definitions(did=None):
    schema()
    if did is not None:_device(did)
    return [{**dict(r),"steps":json.loads(r["steps"]),"enabled":bool(r["enabled"])} for r in store.q("SELECT * FROM routines ORDER BY name")
            if did is None or (r["enabled"] and r["device_id"] in ("",did))]


def save(body,rid=None):
    if not isinstance(body,dict) or set(body)-{"name","enabled","device_id","steps"}:raise ValueError("Unknown routine field")
    name=_text(body.get("name",""),60,"Routine name")
    if not name:raise ValueError("Enter a routine name")
    enabled=body.get("enabled",True)
    if type(enabled) is not bool:raise ValueError("Enabled must be on or off")
    did=body.get("device_id","")
    if not isinstance(did,str):raise ValueError("Choose a device or any permitted device")
    if did:_device(did)
    steps=validate_steps(body.get("steps"))
    with _lock:
        schema();old=_definition(rid) if rid else None
        duplicate=store.q("SELECT id FROM routines WHERE name=? COLLATE NOCASE",(name,))
        if duplicate and (not old or duplicate[0]["id"]!=old["id"]):raise ValueError("That routine name already exists")
        rid=old["id"] if old else "rt_"+secrets.token_hex(8)
        version=old["version"]+1 if old else 1
        store.x("INSERT INTO routines VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,enabled=excluded.enabled,version=excluded.version,device_id=excluded.device_id,steps=excluded.steps,updated=excluded.updated",
                (rid,name,int(enabled),version,did,json.dumps(steps),time.time()))
        return _definition(rid)["id"]


def delete(rid):
    with _lock:
        item=_definition(rid)
        store.x("DELETE FROM routines WHERE id=?",(item["id"],))
        store.x("UPDATE routine_runs SET cancel_requested=1,status=CASE WHEN status='running' AND inflight=1 THEN 'cancelling' ELSE 'cancelled' END WHERE routine_id=? AND status IN ('running','waiting_approval','interrupted')",(item["id"],))


def get_run(run_id,did=None):
    schema();rows=store.q("SELECT * FROM routine_runs WHERE id=?",(run_id,))
    if not rows or (did is not None and rows[0]["device_id"]!=did):raise ValueError("No matching routine run for this device")
    if did is not None:_device(did)
    row=dict(rows[0]);row["steps"]=json.loads(row["steps"]);row["results"]=json.loads(row["results"])
    return row


def runs(did=None):
    schema()
    if did is not None:_device(did)
    order="ORDER BY CASE WHEN status IN ('running','cancelling','waiting_approval','interrupted') THEN 0 ELSE 1 END,created DESC"
    rows=store.q("SELECT id FROM routine_runs "+order+" LIMIT 132") if did is None else store.q("SELECT id FROM routine_runs WHERE device_id=? "+order+" LIMIT 20",(did,))
    return [get_run(r["id"],did) for r in rows]


def _prune():
    cutoff=time.time()-7*86400
    store.x("DELETE FROM routine_runs WHERE status IN ('done','failed','cancelled') AND (updated<? OR id NOT IN (SELECT id FROM routine_runs ORDER BY created DESC LIMIT 100))",(cutoff,))


def _launch(run_id,attempt):threading.Thread(target=_execute,args=(run_id,attempt),daemon=True,name="GhostRoutine").start()


def start(rid,did):
    _device(did)
    with _lock:
        item=_definition(rid)
        if not item["enabled"] or item["device_id"] not in ("",did):raise PermissionError("This routine is disabled or assigned to another device")
        recent=store.q("SELECT id FROM routine_runs WHERE routine_id=? AND device_id=? AND created>? ORDER BY created DESC LIMIT 1",(item["id"],did,time.time()-5))
        if recent:return get_run(recent[0]["id"],did)
        active=store.q("SELECT id FROM routine_runs WHERE device_id=? AND status IN ('running','cancelling','waiting_approval','interrupted')",(did,))
        if active:raise ValueError("This device already has an unfinished routine; resume or cancel it first")
        if len(store.q("SELECT id FROM routine_runs WHERE status IN ('running','cancelling','waiting_approval','interrupted')"))>=32:raise ValueError("Too many unfinished routines")
        _prune();run_id="rr_"+secrets.token_hex(10);now=time.time()
        store.x("INSERT INTO routine_runs(id,routine_id,name,version,device_id,steps,next_step,status,results,created,updated) VALUES(?,?,?,?,?,?,0,'running','[]',?,?)",
                (run_id,item["id"],item["name"],item["version"],did,item["steps"],now,now))
    _launch(run_id,1)
    return get_run(run_id,did)


def resume(run_id,did=None):
    with _lock:
        run=get_run(run_id,did);_device(run["device_id"])
        if run["status"]!="waiting_approval":raise ValueError("Only a routine waiting for approval can be resumed; review interrupted/failed work and cancel it")
        item=_definition(run["routine_id"])
        if not item["enabled"] or item["version"]!=run["version"]:raise ValueError("Routine configuration changed; cancel this run and review a new one")
        attempt=run["attempt"]+1
        store.x("UPDATE routine_runs SET status='running',attempt=?,updated=? WHERE id=?",(attempt,time.time(),run_id))
    _launch(run_id,attempt)
    return get_run(run_id,did)


def cancel(run_id,did=None):
    with _lock:
        run=get_run(run_id,did)
        if run["status"] not in ACTIVE:return run
        status="cancelling" if run["inflight"] and run["status"] in ("running","cancelling") else "cancelled"
        store.x("UPDATE routine_runs SET cancel_requested=1,status=?,updated=? WHERE id=?",(status,time.time(),run_id))
    return get_run(run_id,did)


def recover():
    schema()
    store.x("UPDATE routine_runs SET status='interrupted',attempt=attempt+1,updated=? WHERE status IN ('running','cancelling')",(time.time(),))


def _step(step,did):
    from . import tools,household
    device=_device(did);ctx=tools.Ctx(did,device["name"],"task")
    kind=step["kind"]
    required={"timer":"timer","home":"home_assistant","weather":"weather","briefing":"briefing"}.get(kind)
    if required and not access.tool_allowed(did,required):return "failed","This device's access profile does not allow that step."
    if kind=="message":return "ok",step["text"]
    if kind=="timer":
        timer=household.set_timer(step["seconds"],step["label"],did,"")
        return "ok",f"Timer {step['label'] or timer['id']} set for {step['seconds']} seconds."
    if kind=="home":
        from .tools import home
        eid=step["entity_id"];domain=eid.split('.')[0]
        states=home._req("GET","/api/states").json()
        if not any(s.get("entity_id")==eid for s in states):return "failed","Exact home entity not found; nothing was changed."
        plan={"domain":domain,"service":{"on":"turn_on","off":"turn_off","toggle":"toggle"}[step["action"]],"data":{"entity_id":eid}}
        denial=access.authorize_action(did,"home_service",{"services":[plan]})
        if denial:return ("waiting_approval" if denial.startswith("OWNER APPROVAL NEEDED") else "failed"),denial
        code=home._call(plan["domain"],plan["service"],plan["data"])
        if not 200<=code<300:return "failed","Home Assistant did not confirm success; check the device before running again."
        return "ok",f"{eid}: {step['action']} confirmed by Home Assistant."
    name="briefing" if kind=="briefing" else "weather"
    text=tools.run(name,{"city":step["city"]} if kind=="weather" and step["city"] else {},ctx)
    if text.startswith((f"The {name} tool failed:","Permission denied","Couldn't","Could not","failed:")):
        return "failed",text
    return "ok",text


def _notify(run_id,attempt):
    from .hub import hub
    run=get_run(run_id)
    if run["attempt"]!=attempt or run["status"] not in ("done","failed","waiting_approval","interrupted"):return
    text=f"Routine {run['name']}: {run['status'].replace('_',' ')}. "+" ".join(r["text"] for r in run["results"][-8:])
    hub.send_sync(run["device_id"],{"type":"task_done","status":run["status"],"text":text[:2200],"say":text[:2200]})


def _execute(run_id,attempt):
    try:
        while True:
            with _lock:
                run=get_run(run_id)
                if run["attempt"]!=attempt or run["status"]!="running":return
                _device(run["device_id"]);definition=_definition(run["routine_id"])
                if not definition["enabled"] or definition["version"]!=run["version"]:raise ValueError("Routine changed or was disabled; review before starting again")
                if run["cancel_requested"]:
                    store.x("UPDATE routine_runs SET status='cancelled',updated=? WHERE id=?",(time.time(),run_id));return
                if run["next_step"]>=len(run["steps"]):
                    store.x("UPDATE routine_runs SET status='done',updated=? WHERE id=?",(time.time(),run_id));break
                step=run["steps"][run["next_step"]]
                store.x("UPDATE routine_runs SET inflight=1,updated=? WHERE id=?",(time.time(),run_id))
            status,text=_step(step,run["device_id"])
            with _lock:
                current=get_run(run_id)
                if current["attempt"]!=attempt:return
                results=current["results"]
                # Replace the prior approval explanation, not the already completed steps.
                results=[r for r in results if r["step"]!=run["next_step"]]
                results.append({"step":run["next_step"],"kind":step["kind"],"text":str(text)[:1400],"status":status})
                next_step=run["next_step"]+(1 if status=="ok" else 0)
                state="cancelled" if current["cancel_requested"] else ("running" if status=="ok" else status)
                store.x("UPDATE routine_runs SET inflight=0,next_step=?,status=?,results=?,updated=? WHERE id=?",(next_step,state,json.dumps(results),time.time(),run_id))
                if state!="running":break
    except Exception as exc:
        with _lock:
            current=get_run(run_id)
            if current["attempt"]==attempt:
                results=current["results"]+[{"step":current["next_step"],"kind":"error","status":"failed","text":f"Step not confirmed ({type(exc).__name__}). Review its effect before starting again."}]
                store.x("UPDATE routine_runs SET status='failed',inflight=0,results=?,updated=? WHERE id=?",(json.dumps(results),time.time(),run_id))
    try:_notify(run_id,attempt)
    except Exception:pass  # completed state remains available if the source is offline
