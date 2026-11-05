"""Do Not Disturb by voice: "don't disturb me for two hours", "quiet mode until 7am"."""
from __future__ import annotations

from . import Ctx, tool
from .. import quiet, store


def _say_state(scope: str) -> str:
    st = quiet.state(scope)
    label = "Do Not Disturb" if scope == "me" else "House quiet mode"
    if st["active"]:
        line = f"{label} is on {st['until_text']}."
    else:
        line = f"{label} is off."
    if st["schedule_text"]:
        line += f" It turns on by itself {st['schedule_text']}."
    return line


@tool("quiet_mode",
      """Do Not Disturb. scope 'me' = the owner's own phone and PC go quiet (their personal Do Not Disturb);
      scope 'house' = house quiet mode, every device including the shared hub tablets. While it's on, Ghost holds
      announcements and non-urgent alerts and gives a catch-up afterwards; favourite people, messages with emergency
      words and safety alerts (smoke, gas, leaks, alarms, urgent camera alerts) still get through. Use action 'on'
      with minutes or until (a time like '7am') — or neither for 'until I turn it off'; 'off'; 'status'; or
      'schedule' with schedule like '10pm to 6:30am' (or 'off') for a nightly routine. Also add_favourite /
      remove_favourite a person who should always get through.""",
      {"type": "OBJECT", "properties": {
          "action": {"type": "STRING", "enum": ["on", "off", "status", "schedule", "add_favourite", "remove_favourite"]},
          "scope": {"type": "STRING", "enum": ["me", "house"], "description": "me (default) or house"},
          "minutes": {"type": "NUMBER", "description": "For how long, in minutes"},
          "until": {"type": "STRING", "description": "Until a time of day, e.g. '7am', '18:30'"},
          "schedule": {"type": "STRING", "description": "For action schedule: '10pm to 6:30am', or 'off'"},
          "name": {"type": "STRING", "description": "For add_favourite / remove_favourite: the person"}},
       "required": ["action"]})
def quiet_mode(args: dict, ctx: Ctx) -> str:
    action = str(args.get("action") or "status").lower()
    scope = str(args.get("scope") or "me").lower()
    if scope not in quiet.SCOPES:
        scope = "me"
    try:
        if action == "on":
            minutes = args.get("minutes")
            quiet.set_quiet(scope, True, minutes=float(minutes) if minutes else None, until=args.get("until") or None)
        elif action == "off":
            quiet.set_quiet(scope, False)
        elif action == "schedule":
            quiet.set_schedule(scope, str(args.get("schedule") or ""))
        elif action in ("add_favourite", "remove_favourite"):
            name = str(args.get("name") or "").strip()
            if not name:
                return "Who should I add?"
            names = [n for n in quiet._split(store.get("quiet_favourites"))]
            if action == "add_favourite" and name.lower() not in [n.lower() for n in names]:
                names.append(name)
            if action == "remove_favourite":
                names = [n for n in names if n.lower() != name.lower()]
            store.put("quiet_favourites", ", ".join(names))
            quiet.tick_soon()
            return (f"Favourites who always get through: {', '.join(quiet.favourites()) or 'nobody yet'}.")
        else:
            return _say_state("me") + " " + _say_state("house")
    except ValueError as e:
        return str(e)
    quiet.tick_soon()
    st = _say_state(scope)
    if action == "on":
        st += " Favourites, emergency messages and safety alerts still get through."
    return st
