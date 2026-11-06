"""Meal planner — the week's dinners, and their ingredients onto the shopping list.

"What's for dinner tonight?", "plan dinners for the week", "make Thursday tacos",
"put this week's dinners on the shopping list", "how do I make tonight's dinner?"

One dinner per day, kept on the server and shown on the kitchen tablet. Ingredients come from
Claude when a dish is added without them (for the household size), so "shop for the week"
can put them on the shared shopping list — merged with what's already there, and skipping the
pantry staples set on the admin page (salt, pepper, oil…).

Devices get {"type": "meals", "days": [...]} whenever the plan changes, like timers and lists.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time

from . import household, store

SCHEMA = """
CREATE TABLE IF NOT EXISTS meals (
    day TEXT PRIMARY KEY, title TEXT, ingredients TEXT DEFAULT '[]', notes TEXT DEFAULT '',
    added_by TEXT DEFAULT '', at REAL, shopped INTEGER DEFAULT 0);
"""
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_ready = False


def _schema() -> None:
    global _ready
    if not _ready:
        store._con().executescript(SCHEMA)
        _ready = True


def today() -> dt.date:
    from .quiet import _local
    return _local().date()


def parse_day(s: str, base: dt.date | None = None) -> dt.date | None:
    """'tonight', 'tomorrow', 'thursday', 'next monday', '2026-11-12', '12/11' → a date (the next one)."""
    base = base or today()
    t = str(s or "").strip().lower()
    if t in ("", "today", "tonight", "this evening"):
        return base
    if t in ("tomorrow", "tomorrow night"):
        return base + dt.timedelta(days=1)
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        try:
            return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})", t)               # Australian day/month
    if m:
        try:
            d = dt.date(base.year, int(m.group(2)), int(m.group(1)))
            return d if d >= base else d.replace(year=base.year + 1)
        except ValueError:
            return None
    nxt = t.startswith("next ")
    t = t.replace("next ", "").replace("this ", "").replace(" night", "").strip()
    for i, name in enumerate(WEEKDAYS):
        if t in (name, name[:3]):
            ahead = (i - base.weekday()) % 7
            if nxt and ahead == 0:
                ahead = 7
            return base + dt.timedelta(days=ahead)
    return None


def day_words(d: dt.date, base: dt.date | None = None) -> str:
    base = base or today()
    if d == base:
        return "tonight"
    if d == base + dt.timedelta(days=1):
        return "tomorrow"
    if 0 < (d - base).days < 7:
        return d.strftime("%A")
    return d.strftime("%A %-d %B") if hasattr(d, "strftime") else str(d)


def _row(r) -> dict:
    d = dict(r)
    d["ingredients"] = json.loads(d.get("ingredients") or "[]")
    return d


def get(d: dt.date) -> dict | None:
    _schema()
    rows = store.q("SELECT * FROM meals WHERE day=?", (d.isoformat(),))
    return _row(rows[0]) if rows else None


def week(start: dt.date | None = None, days: int = 7) -> list[dict]:
    """The next `days` days from start (today), each {"day", "label", "title"?, "ingredients"?}."""
    _schema()
    start = start or today()
    end = start + dt.timedelta(days=days - 1)
    have = {r["day"]: _row(r) for r in store.q("SELECT * FROM meals WHERE day BETWEEN ? AND ?",
                                                (start.isoformat(), end.isoformat()))}
    out = []
    for i in range(days):
        d = start + dt.timedelta(days=i)
        m = have.get(d.isoformat()) or {}
        out.append({"day": d.isoformat(), "label": day_words(d, start), "weekday": d.strftime("%a"),
                    "title": m.get("title", ""), "ingredients": m.get("ingredients", []),
                    "notes": m.get("notes", ""), "shopped": bool(m.get("shopped"))})
    return out


def snapshot() -> dict:
    return {"type": "meals", "days": week()}


def _push() -> None:
    household._push(snapshot())


def pantry() -> set[str]:
    return {x.strip().lower() for x in re.split(r"[,\n]", str(store.get("meal_pantry") or "")) if x.strip()}


def in_pantry(item: str) -> bool:
    i = item.lower()
    return any(p == i or re.search(rf"\b{re.escape(p)}\b", i) for p in pantry())


# ── Claude helps: ingredients, ideas, how to cook it ─────────────────────────
def _ask(prompt: str, max_tokens: int = 700) -> str:
    from .tools.brain import claude
    text, _ = claude(fast=True).complete([{"role": "user", "content": prompt}], max_tokens=max_tokens)
    return text or ""


def _household() -> str:
    n = store.get("meal_serves") or 4
    pref = str(store.get("meal_preferences") or "").strip()
    return f"for {n} people" + (f"; the household's preferences: {pref}" if pref else "")


def ingredients_for(title: str) -> list[str]:
    """A shopping-style ingredient list for one dinner ([] if Claude isn't available)."""
    try:
        text = _ask(f"Dinner: {title}, {_household()}. List the ingredients to buy, as a JSON array of short "
                    "shopping-list strings with amounts, e.g. [\"500 g beef mince\", \"1 onion\", \"2 tins chopped "
                    "tomatoes\"]. Australian names and metric. JSON only.", 400)
        m = re.search(r"\[.*\]", text, re.S)
        items = json.loads(m.group(0)) if m else []
        return [str(x).strip()[:80] for x in items if str(x).strip()][:30]
    except Exception:  # noqa: BLE001
        return []


def suggest(dates: list[dt.date], ideas: str = "") -> list[dict]:
    """Claude plans dinners for these dates, avoiding the last fortnight's meals."""
    _schema()
    recent = [r["title"] for r in store.q("SELECT title FROM meals WHERE day >= ? ORDER BY day",
                                          ((today() - dt.timedelta(days=14)).isoformat(),)) if r["title"]]
    days = ", ".join(d.strftime("%A") for d in dates)
    text = _ask(f"Plan family dinners {_household()} for: {days}. "
                + (f"They'd like: {ideas}. " if ideas else "")
                + (f"Don't repeat these recent dinners: {', '.join(recent)}. " if recent else "")
                + "Easy weeknight cooking, some variety, quicker meals mid-week. Reply with JSON only: "
                  "[{\"day\": \"Monday\", \"title\": \"Spaghetti bolognese\", \"ingredients\": [\"500 g beef mince\", "
                  "\"1 onion\"]}, …] — one per day, in that order, ingredients as short shopping-list strings with "
                  "amounts (Australian names, metric).", 2500)
    m = re.search(r"\[.*\]", text, re.S)
    try:
        items = json.loads(m.group(0)) if m else []
    except Exception:  # noqa: BLE001
        items = []
    out = []
    for d, it in zip(dates, items):
        if isinstance(it, dict) and it.get("title"):
            out.append({"day": d, "title": str(it["title"])[:80],
                        "ingredients": [str(x)[:80] for x in (it.get("ingredients") or [])][:30]})
    return out


def recipe(title: str) -> str:
    return _ask(f"How do I cook {title} {_household()}? Short steps a home cook can follow while cooking, "
                "spoken aloud — no markdown, numbered steps, under 180 words.", 600).strip()


# ── changing the plan ────────────────────────────────────────────────────────
def set_meal(d: dt.date, title: str, ingredients: list[str] | None = None, by: str = "",
             notes: str = "") -> dict:
    _schema()
    title = " ".join(str(title or "").split())[:80]
    if ingredients is None:
        ingredients = ingredients_for(title)
    store.x("INSERT OR REPLACE INTO meals (day, title, ingredients, notes, added_by, at, shopped) "
            "VALUES (?,?,?,?,?,?,0)", (d.isoformat(), title, json.dumps(ingredients), notes, by, time.time()))
    _push()
    return get(d) or {}


def clear(d: dt.date) -> bool:
    _schema()
    had = get(d) is not None
    store.x("DELETE FROM meals WHERE day=?", (d.isoformat(),))
    if had:
        _push()
    return had


def shop(dates: list[dt.date], by: str = "") -> dict:
    """Put these dinners' ingredients on the shopping list. Returns {"added": [...], "skipped": [...]}."""
    _schema()
    added, skipped, meals_done = [], [], []
    for d in dates:
        m = get(d)
        if not m or not m["title"]:
            continue
        ing = m["ingredients"] or ingredients_for(m["title"])
        if ing and not m["ingredients"]:
            store.x("UPDATE meals SET ingredients=? WHERE day=?", (json.dumps(ing), d.isoformat()))
        want = [i for i in ing if not in_pantry(i)]
        skipped += [i for i in ing if in_pantry(i)]
        added += household.add_items("shopping", want, by or "meal planner")
        store.x("UPDATE meals SET shopped=1 WHERE day=?", (d.isoformat(),))
        meals_done.append(m["title"])
    _push()
    return {"added": added, "skipped": sorted(set(skipped)), "meals": meals_done}
