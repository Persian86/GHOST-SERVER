"""Timers, alarms, lists, notes and 'who's here', as tools for every Ghost device."""
from __future__ import annotations

import time
import math

from . import Ctx, tool
from .. import faces, household as hh
from ..timer_selection import select as select_timers


def _duration(args: dict, default: float = 0) -> float | None:
    if not any(k in args for k in ("hours", "minutes", "seconds")):
        return default
    total = 0.
    for key, scale in (("hours", 3600), ("minutes", 60), ("seconds", 1)):
        raw = args.get(key, 0)
        if isinstance(raw, bool):
            return None
        try:
            value = float(raw or 0)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(value) or value < 0:
            return None
        total += value * scale
    return total if math.isfinite(total) and 1 <= total <= 24 * 3600 else None


def _where(ctx: Ctx) -> tuple[str, str]:
    return ctx.device_id, str(ctx.extra.get("room") or "")


def _who(ctx: Ctx) -> str:
    p = faces.here(ctx.device_id.split("~", 1)[0])
    return p["names"][0] if p and p["names"] else ""


@tool("timer", """Timers and alarms, which ring on the device they were set on (or that room).
Set a timer: action=set with hours/minutes/seconds and an optional label ('pasta').
Set an alarm: action=alarm with time ('6:30am', '18:00'), optional label, and days
('weekdays', 'weekends', 'every day', 'mon,wed', or empty for just once).
Also: list, stop (silence a ringing one, or cancel; 'all' for everything), snooze (ringing only),
add (more time). Give the timer's name when several exist. Ambiguous commands ask which one;
never guess or repeat an action after it succeeds. kind can restrict selection to timer or alarm.""",
      {"type": "OBJECT", "properties": {
          "action": {"type": "STRING", "description": "set | alarm | list | stop | snooze | add"},
          "hours": {"type": "NUMBER"}, "minutes": {"type": "NUMBER"}, "seconds": {"type": "NUMBER"},
          "label": {"type": "STRING", "description": "Name of the timer/alarm, or which one to stop/add to"},
          "kind": {"type": "STRING", "description": "Optional target type: timer | alarm"},
          "time": {"type": "STRING", "description": "Alarm time of day"},
          "days": {"type": "STRING", "description": "Repeat days for an alarm"}},
       "required": ["action"]})
def timer(args: dict, ctx: Ctx) -> str:
    a = str(args.get("action", "")).lower()
    label = str(args.get("label") or "").strip()
    did, room = _where(ctx)
    if a in ("set", "start", "timer"):
        secs = _duration(args)
        if not secs:
            return "How long should the timer be? Use a duration from one second to 24 hours."
        t = hh.set_timer(secs, label, did, room)
        return f"{(t['label'] + ' timer') if t['label'] else 'Timer'} set for {hh._dur(secs)}."
    if a in ("alarm", "set_alarm", "wake"):
        r = hh.set_alarm(str(args.get("time") or ""), label, str(args.get("days") or ""), did, room)
        if not r:
            return "I didn't catch the alarm time. Say it like '6:30am'."
        from ..quiet import _local, _clock_words
        when = _local(r["ends_at"])
        rep = f", repeating {r['days'].replace(',', ', ')}" if r["days"] else ""
        return f"Alarm set for {_clock_words(when.hour, when.minute)} {when.strftime('%A')}{rep}."
    if a in ("list", "show", "status", "check"):
        live = hh.timers()
        kind = str(args.get('kind') or '').lower()
        if kind in ('timer','alarm'):
            live = [t for t in live if t['kind']==kind]
        return "; ".join(hh.describe(t) for t in live) if live else "No timers or alarms are set."
    if a in ("stop", "cancel", "dismiss", "off", "snooze"):
        targets, question = select_timers(hh.timers(), label, a, did, str(args.get("kind") or ""))
        if question:
            return question
        if a == "snooze":
            secs = _duration(args, 9 * 60)
            if not secs:
                return "How long should I snooze it? Use a duration from one second to 24 hours."
            changed = sum(hh.add_time(t["id"], secs) for t in targets)
            return f"Snoozed for {hh._dur(secs)}." if changed == len(targets) else "Some selected timers or alarms changed before I could snooze them. Please check the timer controls."
        changed = 0
        for t in targets:
            if t["status"] == "ringing" or t["kind"] == "timer":
                changed += bool(hh.stop(t["id"]))
            else:
                changed += bool(hh.cancel_alarm_forever(t["id"]))
        if changed != len(targets):
            return "Some selected timers or alarms changed before I could stop them. Please check the timer controls."
        name = (targets[0]["label"] + " " + targets[0]["kind"]).strip()
        return f"Stopped {changed}." if changed > 1 else f"Stopped the {name}."
    if a in ("add", "extend", "more"):
        secs = _duration(args)
        if not secs:
            return "How much more time? Use a duration from one second to 24 hours."
        targets, question = select_timers(hh.timers(), label, a, did, str(args.get("kind") or ""))
        if question:
            return question
        changed = sum(hh.add_time(t["id"], secs) for t in targets)
        return f"Added {hh._dur(secs)}." if changed == len(targets) else "Some selected timers or alarms changed before I could extend them. Please check the timer controls."
    return "Use set, alarm, list, stop, snooze or add."


@tool("lists", """The household's shared lists (shopping, to do, or any other name). They show on the
kitchen tablet and every phone. action: add (items, comma-separated), remove, done (tick off),
show, clear (remove ticked-off items).""",
      {"type": "OBJECT", "properties": {
          "action": {"type": "STRING", "description": "add | remove | done | show | clear"},
          "list": {"type": "STRING", "description": "shopping (default), to do, or another list's name"},
          "items": {"type": "STRING", "description": "Item(s), comma separated"}},
       "required": ["action"]})
def lists(args: dict, ctx: Ctx) -> str:
    a = str(args.get("action", "")).lower()
    name = hh._list_name(str(args.get("list") or "shopping"))
    items = str(args.get("items") or "")
    if a in ("add", "put"):
        added = hh.add_items(name, items, _who(ctx) or ctx.device_name)
        return f"Added {', '.join(added)} to the {name} list." if added else "That's already on the list."
    if a in ("remove", "delete", "take off"):
        hits = hh._match_items(name, items)
        for h in hits:
            hh.set_item(h["id"], delete=True)
        return f"Removed {', '.join(h['text'] for h in hits)}." if hits else "I couldn't find that on the list."
    if a in ("done", "tick", "got", "check"):
        hits = hh._match_items(name, items)
        for h in hits:
            hh.set_item(h["id"], done=True)
        return f"Ticked off {', '.join(h['text'] for h in hits)}." if hits else "I couldn't find that on the list."
    if a in ("clear", "clean"):
        return f"Cleared {hh.clear_done(name)} ticked-off item(s)."
    rows = [r for r in hh.lists().get(name, []) if not r["done"]]
    return f"The {name} list: " + ", ".join(r["text"] for r in rows) if rows else f"The {name} list is empty."


@tool("notes", """Notes the family leave for each other, shown on the kitchen tablet (to the right
person when the camera recognises them). action: leave (text, to = a name or 'everyone'),
read (for = a name; default whoever is here), done (mark a note read: give words from it).""",
      {"type": "OBJECT", "properties": {
          "action": {"type": "STRING", "description": "leave | read | done"},
          "text": {"type": "STRING"}, "to": {"type": "STRING"}, "for": {"type": "STRING"},
          "from": {"type": "STRING", "description": "Who it's from, if they said"}},
       "required": ["action"]})
def notes(args: dict, ctx: Ctx) -> str:
    a = str(args.get("action", "")).lower()
    if a in ("leave", "add", "write"):
        text = str(args.get("text") or "").strip()
        if not text:
            return "What should the note say?"
        r = hh.leave_note(text, str(args.get("to") or ""), str(args.get("from") or _who(ctx) or ""))
        return f"Note left for {r['to']}."
    if a in ("done", "read_done", "clear", "delete"):
        words = str(args.get("text") or "").lower()
        hits = [n for n in hh.notes() if words and words in n["text"].lower()]
        for n in hits:
            hh.done_note(n["id"])
        return f"Marked {len(hits)} note(s) as read." if hits else "I couldn't find that note."
    who = str(args.get("for") or _who(ctx) or "")
    rows = hh.notes(who)
    if not rows:
        return "No notes" + (f" for {who}." if who else ".")
    return "; ".join(f"{('From ' + n['from_name'] + ': ') if n['from_name'] else ''}{n['text']}"
                     + (f" (for {n['to_name']})" if n['to_name'] and not who else "") for n in rows[:8])


@tool("who_is_here", """Who Ghost's cameras have recognised recently and where, e.g. 'is Adam home?',
'who's in the kitchen?'. Only people set up in Visual ID can be recognised.""")
def who_is_here(args: dict, ctx: Ctx) -> str:
    seen = faces.last_seen()
    if not seen:
        return "I haven't recognised anyone recently."
    return "; ".join(f"{e['name']} at the {e['device']} {_ago(e['at'])}" for e in seen)


def _ago(t: float) -> str:
    m = int((time.time() - t) // 60)
    return "just now" if m < 2 else f"{m} minutes ago" if m < 60 else f"{m // 60} hour(s) ago"


@tool("meals", """The family meal planner (dinners), shown on the kitchen tablet.
  show — what's for dinner (day = tonight, tomorrow, a weekday, or 'week')
  set — put a dish on a day ('make Thursday tacos'); ingredients are worked out automatically
  plan — fill empty days of the coming week with ideas (ideas = anything they asked for)
  clear — take a day off the plan
  shop — put the ingredients for the week (or day) on the shopping list, skipping pantry staples
  recipe — how to cook a day's dinner (or a dish)""",
      {"type": "OBJECT", "properties": {
          "action": {"type": "STRING", "description": "show | set | plan | clear | shop | recipe"},
          "day": {"type": "STRING", "description": "tonight (default), tomorrow, monday…, or 'week'"},
          "dish": {"type": "STRING", "description": "For set/recipe: the dinner, e.g. 'spaghetti bolognese'"},
          "ideas": {"type": "STRING", "description": "For plan: e.g. 'something with chicken, a fish night'"}},
       "required": ["action"]})
def meals_tool(args: dict, ctx: Ctx) -> str:
    import datetime as dt
    from .. import meals as m
    a = str(args.get("action") or "show").lower()
    day = str(args.get("day") or "").strip().lower()
    whole_week = day in ("week", "this week", "the week", "next 7 days", "all")
    if a == "plan":
        empty = [dt.date.fromisoformat(x["day"]) for x in m.week() if not x["title"]]
        if not empty:
            return "Every night this week already has a dinner. Clear a day first if you want a new idea."
        try:
            ideas = m.suggest(empty, str(args.get("ideas") or ""))
        except Exception as e:  # noqa: BLE001
            return f"I couldn't plan dinners right now ({e})."
        for i in ideas:
            m.set_meal(i["day"], i["title"], i["ingredients"], _who(ctx) or ctx.device_name)
        return ("Planned: " + "; ".join(f"{m.day_words(i['day'])} {i['title']}" for i in ideas)
                + ". Say 'add them to the shopping list' when you're happy.") if ideas else "I couldn't come up with a plan."
    if a == "shop":
        dates = [dt.date.fromisoformat(x["day"]) for x in m.week()] if (whole_week or not day) else \
            [d for d in [m.parse_day(day)] if d]
        r = m.shop(dates, _who(ctx) or ctx.device_name)
        if not r["meals"]:
            return "There are no dinners planned to shop for. Plan some first."
        skip = f" Skipped pantry staples: {', '.join(r['skipped'])}." if r["skipped"] else ""
        return (f"Added {len(r['added'])} item(s) to the shopping list for {', '.join(r['meals'])}." if r["added"]
                else f"Everything for {', '.join(r['meals'])} is already on the list.") + skip
    if whole_week:
        rows = m.week()
        return "Dinners: " + "; ".join(f"{x['label']}: {x['title'] or '—'}" for x in rows)
    d = m.parse_day(day or "tonight")
    if d is None:
        return f"Which day is '{day}'?"
    if a in ("set", "add", "make", "change"):
        dish = str(args.get("dish") or "").strip()
        if not dish:
            return "What's for dinner that day?"
        r = m.set_meal(d, dish, by=_who(ctx) or ctx.device_name)
        extra = f" ({len(r.get('ingredients') or [])} ingredients ready for the shopping list)" if r.get("ingredients") else ""
        return f"{m.day_words(d).capitalize()}: {r.get('title', dish)}.{extra}"
    if a in ("clear", "remove", "delete"):
        return f"Took {m.day_words(d)} off the plan." if m.clear(d) else f"Nothing was planned for {m.day_words(d)}."
    meal = m.get(d)
    if a == "recipe":
        dish = str(args.get("dish") or "").strip() or (meal or {}).get("title", "")
        if not dish:
            return f"Nothing's planned for {m.day_words(d)} — which dish?"
        try:
            return m.recipe(dish) or "I couldn't get a recipe right now."
        except Exception as e:  # noqa: BLE001
            return f"I couldn't get a recipe right now ({e})."
    if not meal or not meal["title"]:
        return f"Nothing's planned for {m.day_words(d)} yet."
    return f"{m.day_words(d).capitalize()} it's {meal['title']}."
