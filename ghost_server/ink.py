"""Handwritten notes, handwriting reading and the pantry catalogue.

* Handwritten notes: the tablet sends the drawing (pen or finger) as a PNG. It is
  kept beside the database (notes_ink/<random>.png) and shown as drawn; Claude reads
  it once so Ghost can say what it says ("read my notes").
* Handwriting for lists: "milk / eggs / bread" written on the shopping page comes
  back as separate items for the person to confirm.
* Pantry: a picture catalogue of everyday groceries by aisle (emoji pictures, so it
  works offline and needs no image downloads), plus items you add yourself and what
  you buy most often.
"""
from __future__ import annotations

import base64
import re
import secrets
from pathlib import Path

from . import store

MAX_PNG = 3 * 1024 * 1024
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_NAME = re.compile(r"^[A-Za-z0-9_-]{16,64}\.png$")


def ink_dir() -> Path:
    from . import config
    base = Path(getattr(store, "_db_path", None) or config.DB_PATH).parent
    d = base / "notes_ink"
    d.mkdir(parents=True, exist_ok=True)
    return d


def decode_png(data: str) -> bytes:
    """A base64 PNG from a device, checked: real PNG, not too big."""
    try:
        raw = base64.b64decode(str(data or "").split(",", 1)[-1], validate=True)
    except Exception as e:  # noqa: BLE001
        raise ValueError("the drawing wasn't a valid image") from e
    if not raw.startswith(PNG_MAGIC):
        raise ValueError("the drawing wasn't a PNG image")
    if len(raw) > MAX_PNG:
        raise ValueError("the drawing is too big")
    return raw


def save_ink(png: bytes) -> str:
    name = secrets.token_urlsafe(18) + ".png"
    (ink_dir() / name).write_bytes(png)
    return name


def ink_path(name: str) -> Path | None:
    if not _NAME.match(str(name or "")):
        return None
    p = ink_dir() / name
    return p if p.is_file() else None


def delete_ink(name: str) -> None:
    p = ink_path(name)
    if p:
        try:
            p.unlink()
        except OSError:
            pass


def read_handwriting(png: bytes, mode: str = "note") -> str:
    """What the handwriting says. mode "list": one item per line. Empty if it can't be read."""
    from .tools.brain import claude
    ask = ("This is a shopping list written by hand on a tablet. Write out each item on its own line, "
           "exactly as written but with spelling fixed, no bullets, no numbering, no extra words. "
           "If something is a drawing rather than words, skip it." if mode == "list" else
           "This is a handwritten note from a family's kitchen tablet. Write out exactly what it says, "
           "with spelling fixed, as plain text. If it is only a drawing, describe it in a few words "
           "in brackets, e.g. (a drawing of a heart).")
    img = base64.b64encode(png).decode()
    try:
        text, _ = claude(fast=True).complete([{"role": "user", "content": [
            {"type": "text", "text": ask},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img}"}}]}], max_tokens=400)
    except Exception:  # noqa: BLE001
        return ""
    return str(text or "").strip()


def list_items(text: str) -> list[str]:
    out = []
    for line in re.split(r"[\n,;]+", text or ""):
        item = re.sub(r"^[\s\-*•\d.)]+", "", line).strip()
        if item and item.lower() not in {i.lower() for i in out} and len(item) <= 60:
            out.append(item)
    return out[:40]


# ── pantry ───────────────────────────────────────────────────────────────────
PANTRY: dict[str, list[tuple[str, str]]] = {
    "Fruit": [("Apples", "🍎"), ("Bananas", "🍌"), ("Oranges", "🍊"), ("Mandarins", "🍊"), ("Lemons", "🍋"),
              ("Limes", "🍋"), ("Grapes", "🍇"), ("Strawberries", "🍓"), ("Blueberries", "🫐"), ("Watermelon", "🍉"),
              ("Rockmelon", "🍈"), ("Pineapple", "🍍"), ("Mango", "🥭"), ("Pears", "🍐"), ("Peaches", "🍑"),
              ("Cherries", "🍒"), ("Kiwi fruit", "🥝"), ("Avocado", "🥑"), ("Coconut", "🥥")],
    "Vegetables": [("Potatoes", "🥔"), ("Sweet potato", "🍠"), ("Carrots", "🥕"), ("Onions", "🧅"), ("Garlic", "🧄"),
                   ("Tomatoes", "🍅"), ("Lettuce", "🥬"), ("Spinach", "🥬"), ("Broccoli", "🥦"), ("Cucumber", "🥒"),
                   ("Capsicum", "🫑"), ("Chilli", "🌶"), ("Corn", "🌽"), ("Mushrooms", "🍄"), ("Zucchini", "🥒"),
                   ("Eggplant", "🍆"), ("Pumpkin", "🎃"), ("Peas", "🫛"), ("Ginger", "🫚"), ("Fresh herbs", "🌿")],
    "Dairy & eggs": [("Milk", "🥛"), ("Eggs", "🥚"), ("Butter", "🧈"), ("Cheese", "🧀"), ("Yoghurt", "🥛"),
                     ("Cream", "🥛"), ("Sour cream", "🥛"), ("Cream cheese", "🧀"), ("Parmesan", "🧀"),
                     ("Plant milk", "🥛")],
    "Meat & seafood": [("Chicken", "🍗"), ("Chicken breast", "🍗"), ("Mince", "🥩"), ("Steak", "🥩"),
                       ("Lamb", "🍖"), ("Pork", "🥓"), ("Bacon", "🥓"), ("Sausages", "🌭"), ("Ham", "🍖"),
                       ("Fish", "🐟"), ("Salmon", "🍣"), ("Prawns", "🍤"), ("Tuna", "🐟")],
    "Bakery": [("Bread", "🍞"), ("Wraps", "🫓"), ("Bread rolls", "🥖"), ("Croissants", "🥐"), ("Bagels", "🥯"),
               ("Muffins", "🧁"), ("Cake", "🍰"), ("Crumpets", "🥞")],
    "Pantry": [("Pasta", "🍝"), ("Rice", "🍚"), ("Noodles", "🍜"), ("Flour", "🌾"), ("Sugar", "🍬"),
               ("Cereal", "🥣"), ("Oats", "🥣"), ("Peanut butter", "🥜"), ("Honey", "🍯"), ("Jam", "🍓"),
               ("Vegemite", "🫙"), ("Olive oil", "🫒"), ("Pasta sauce", "🍅"), ("Tinned tomatoes", "🥫"),
               ("Baked beans", "🥫"), ("Soup", "🍲"), ("Stock", "🥣"), ("Salt", "🧂"), ("Spices", "🧂"),
               ("Tomato sauce", "🍅"), ("Mayonnaise", "🥚"), ("Coffee", "☕"), ("Tea", "🍵"), ("Milo", "🍫")],
    "Frozen": [("Frozen peas", "🫛"), ("Frozen veg", "🥦"), ("Chips", "🍟"), ("Ice cream", "🍨"), ("Pizza", "🍕"),
               ("Fish fingers", "🐟"), ("Frozen berries", "🫐"), ("Ice", "🧊"), ("Dumplings", "🥟")],
    "Drinks": [("Water", "💧"), ("Juice", "🧃"), ("Soft drink", "🥤"), ("Sparkling water", "🫧"),
               ("Cordial", "🧃"), ("Beer", "🍺"), ("Wine", "🍷"), ("Kombucha", "🍾")],
    "Snacks": [("Chips (crisps)", "🥔"), ("Biscuits", "🍪"), ("Chocolate", "🍫"), ("Lollies", "🍬"), ("Popcorn", "🍿"),
               ("Crackers", "🍘"), ("Muesli bars", "🥜"), ("Nuts", "🥜"), ("Pretzels", "🥨")],
    "Household": [("Toilet paper", "🧻"), ("Paper towel", "🧻"), ("Dishwashing liquid", "🧽"),
                  ("Dishwasher tablets", "🫧"), ("Laundry liquid", "🧺"), ("Bin bags", "🗑"), ("Cling wrap", "📦"),
                  ("Foil", "📦"), ("Sponges", "🧽"), ("Batteries", "🔋"), ("Light globes", "💡")],
    "Personal care": [("Toothpaste", "🪥"), ("Toothbrush", "🪥"), ("Shampoo", "🧴"), ("Conditioner", "🧴"),
                      ("Soap", "🧼"), ("Body wash", "🧴"), ("Deodorant", "🧴"), ("Tissues", "🤧"),
                      ("Sunscreen", "🧴"), ("Band-aids", "🩹"), ("Panadol", "💊")],
    "Kids & pets": [("Nappies", "👶"), ("Wipes", "🧻"), ("Lunchbox snacks", "🍱"), ("Dog food", "🐕"),
                    ("Cat food", "🐈"), ("Kitty litter", "🐈")],
}


def _schema() -> None:
    from . import household
    household._schema()                       # list_items, for "often bought"
    store._con().executescript("""
CREATE TABLE IF NOT EXISTS pantry_custom (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, category TEXT, emoji TEXT, at REAL);""")


def category_of(name: str) -> str:
    n = str(name or "").strip().lower()
    for cat, items in PANTRY.items():
        if any(i.lower() == n for i, _ in items):
            return cat
    _schema()
    row = store.q("SELECT category FROM pantry_custom WHERE lower(name)=? LIMIT 1", (n,))
    return row[0]["category"] if row else ""


def emoji_of(name: str) -> str:
    n = str(name or "").strip().lower()
    for items in PANTRY.values():
        for i, e in items:
            if i.lower() == n:
                return e
    _schema()
    row = store.q("SELECT emoji FROM pantry_custom WHERE lower(name)=? LIMIT 1", (n,))
    return row[0]["emoji"] if row else ""


def pantry() -> dict:
    """Aisles with items, your own items, and what you buy most often."""
    _schema()
    cats = {c: [{"name": n, "emoji": e} for n, e in items] for c, items in PANTRY.items()}
    for r in store.q("SELECT name, category, emoji FROM pantry_custom ORDER BY id"):
        cats.setdefault(r["category"] or "My items", []).append({"name": r["name"], "emoji": r["emoji"] or "🛒", "custom": True})
    recent = [{"name": r["text"], "emoji": emoji_of(r["text"]) or "🛒", "count": r["n"]} for r in store.q(
        "SELECT text, COUNT(*) AS n FROM list_items WHERE list='shopping' GROUP BY lower(text) "
        "ORDER BY n DESC, MAX(at) DESC LIMIT 24")]
    return {"categories": [{"name": c, "items": i} for c, i in cats.items()], "often": recent}


def add_custom(name: str, category: str = "", emoji: str = "") -> dict:
    _schema()
    name = " ".join(str(name or "").split())[:40]
    if not name:
        raise ValueError("the item needs a name")
    cat = " ".join(str(category or "").split())[:30] or "My items"
    emo = str(emoji or "").strip()[:4] or "🛒"
    if not store.q("SELECT id FROM pantry_custom WHERE lower(name)=?", (name.lower(),)):
        import time
        store.x("INSERT INTO pantry_custom (name, category, emoji, at) VALUES (?,?,?,?)", (name, cat, emo, time.time()))
    return {"name": name, "category": cat, "emoji": emo}
