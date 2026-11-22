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
# Aisle → (emoji used when an item has none of its own, "Item|emoji; Item; …")
_AISLES: dict[str, tuple[str, str]] = {
    "Fruit": ("🍎", "Apples|🍎; Green apples|🍏; Bananas|🍌; Oranges|🍊; Mandarins|🍊; Lemons|🍋; Limes|🍋; Grapes|🍇; "
              "Strawberries|🍓; Blueberries|🫐; Raspberries; Blackberries; Watermelon|🍉; Rockmelon|🍈; Honeydew|🍈; "
              "Pineapple|🍍; Mango|🥭; Pears|🍐; Peaches|🍑; Nectarines|🍑; Apricots|🍑; Plums; Cherries|🍒; "
              "Kiwi fruit|🥝; Avocado|🥑; Coconut|🥥; Passionfruit; Papaya; Pomegranate; Dates; Figs; Grapefruit|🍊"),
    "Vegetables": ("🥕", "Potatoes|🥔; Sweet potato|🍠; Carrots|🥕; Onions|🧅; Red onion|🧅; Spring onions|🧅; Garlic|🧄; "
                   "Tomatoes|🍅; Cherry tomatoes|🍅; Lettuce|🥬; Cos lettuce|🥬; Baby spinach|🥬; Kale|🥬; Rocket|🥬; "
                   "Salad mix|🥗; Broccoli|🥦; Broccolini|🥦; Cauliflower|🥦; Cabbage|🥬; Cucumber|🥒; Capsicum|🫑; "
                   "Chilli|🌶; Corn|🌽; Mushrooms|🍄; Zucchini|🥒; Eggplant|🍆; Pumpkin|🎃; Butternut pumpkin|🎃; "
                   "Peas|🫛; Snow peas|🫛; Green beans|🫛; Asparagus; Celery; Leek; Beetroot; Ginger|🫚; Bok choy|🥬; "
                   "Bean sprouts; Fresh herbs|🌿; Basil|🌿; Parsley|🌿; Coriander|🌿; Mint|🌿"),
    "Bakery": ("🍞", "Bread|🍞; Wholemeal bread|🍞; Sourdough|🍞; Raisin bread|🍞; Bread rolls|🥖; Baguette|🥖; "
               "Wraps|🫓; Pita bread|🫓; Naan|🫓; Croissants|🥐; Bagels|🥯; English muffins|🧁; Crumpets|🥞; "
               "Hot cross buns; Muffins|🧁; Donuts|🍩; Cake|🍰; Cupcakes|🧁; Pizza bases|🍕; Burger buns|🍔; Hot dog rolls|🌭"),
    "Dairy & eggs": ("🥛", "Milk|🥛; Lite milk|🥛; Lactose-free milk|🥛; Almond milk|🥛; Oat milk|🥛; Soy milk|🥛; "
                     "Eggs|🥚; Butter|🧈; Margarine|🧈; Yoghurt|🥛; Greek yoghurt|🥛; Kids yoghurt pouches; Cream|🥛; "
                     "Thickened cream|🥛; Sour cream|🥛; Custard; Dips"),
    "Cheese": ("🧀", "Cheese|🧀; Tasty cheese|🧀; Grated cheese|🧀; Cheese slices|🧀; Mozzarella|🧀; Parmesan|🧀; "
               "Feta|🧀; Cream cheese|🧀; Haloumi|🧀; Brie|🧀; Camembert|🧀; Ricotta|🧀; Cheese sticks|🧀; Cottage cheese|🧀"),
    "Meat": ("🥩", "Chicken breast|🍗; Chicken thighs|🍗; Chicken drumsticks|🍗; Whole chicken|🍗; Chicken wings|🍗; "
             "Beef mince|🥩; Steak|🥩; Roast beef|🥩; Stir-fry beef|🥩; Lamb chops|🍖; Lamb roast|🍖; Pork chops|🥓; "
             "Pork belly|🥓; Bacon|🥓; Sausages|🌭; Chicken sausages|🌭; Meatballs; Burger patties|🍔; Schnitzels|🍗; "
             "Rissoles; Chicken mince|🍗; Pork mince|🥩"),
    "Seafood": ("🐟", "Salmon|🍣; Fish fillets|🐟; Barramundi|🐟; Prawns|🍤; Calamari|🦑; Mussels; Smoked salmon|🍣; "
                "Crumbed fish|🐟; Tuna steaks|🐟"),
    "Deli": ("🥓", "Ham|🍖; Shaved ham|🍖; Salami; Chicken (cooked)|🍗; Roast chicken|🍗; Turkey slices; Olives|🫒; "
             "Hummus; Antipasto; Kabana; Pâté"),
    "Breakfast": ("🥣", "Cereal|🥣; Weet-Bix|🥣; Cornflakes|🥣; Rice Bubbles|🥣; Muesli|🥣; Granola|🥣; Oats|🥣; "
                  "Porridge sachets|🥣; Pancake mix|🥞; Maple syrup|🍁; Honey|🍯; Jam|🍓; Peanut butter|🥜; Nutella|🍫; "
                  "Vegemite|🫙; Spreads|🫙"),
    "Pasta, rice & grains": ("🍝", "Pasta|🍝; Spaghetti|🍝; Penne|🍝; Fettuccine|🍝; Lasagne sheets|🍝; Rice|🍚; "
                             "Basmati rice|🍚; Jasmine rice|🍚; Brown rice|🍚; Microwave rice|🍚; Noodles|🍜; "
                             "Instant noodles|🍜; Rice noodles|🍜; Couscous; Quinoa; Gnocchi"),
    "Tins & jars": ("🥫", "Tinned tomatoes|🥫; Tomato paste|🥫; Pasta sauce|🍅; Baked beans|🥫; Chickpeas|🥫; "
                    "Kidney beans|🥫; Lentils|🥫; Tuna|🐟; Salmon (tinned)|🐟; Sweet corn|🌽; Coconut milk|🥥; "
                    "Soup|🍲; Stock|🥣; Tinned fruit|🍑; Beetroot (tinned)|🥫; Pickles|🥒; Sun-dried tomatoes|🍅"),
    "Sauces & condiments": ("🧴", "Tomato sauce|🍅; BBQ sauce; Mayonnaise|🥚; Mustard; Soy sauce; Sweet chilli sauce|🌶; "
                            "Hot sauce|🌶; Worcestershire sauce; Oyster sauce; Salad dressing|🥗; Gravy; Vinegar; "
                            "Balsamic vinegar; Pesto|🌿; Curry paste|🍛; Taco seasoning|🌮; Relish"),
    "Baking": ("🧁", "Flour|🌾; Self-raising flour|🌾; Sugar|🍬; Brown sugar|🍬; Icing sugar|🍬; Baking powder; "
               "Bicarb soda; Vanilla essence; Cocoa|🍫; Choc chips|🍫; Yeast; Cake mix|🎂; Sprinkles; Food colouring; "
               "Desiccated coconut|🥥; Golden syrup|🍯; Condensed milk|🥛; Gelatine"),
    "Oils, herbs & spices": ("🧂", "Olive oil|🫒; Vegetable oil; Canola spray; Coconut oil|🥥; Sesame oil; Salt|🧂; "
                             "Pepper|🧂; Garlic powder|🧄; Paprika|🌶; Cumin; Cinnamon; Mixed herbs|🌿; Oregano|🌿; "
                             "Curry powder|🍛; Chilli flakes|🌶; Stock cubes"),
    "International": ("🌏", "Taco kit|🌮; Tortillas|🫓; Salsa|🍅; Corn chips|🌽; Sushi rice|🍣; Nori|🍙; "
                      "Rice paper; Teriyaki sauce; Fish sauce; Coconut cream|🥥; Pappadums; Dumplings|🥟; Kimchi"),
    "Snacks": ("🍿", "Chips|🥔; Corn chips|🌽; Popcorn|🍿; Crackers|🍘; Rice crackers|🍘; Pretzels|🥨; Nuts|🥜; "
               "Trail mix|🥜; Muesli bars|🥜; Protein bars; Fruit snacks|🍓; Sultanas|🍇; Dried fruit; Seaweed snacks; "
               "Biscuits|🍪; Tim Tams|🍫; Shapes|🍘; Kids lunchbox snacks|🍱"),
    "Sweets": ("🍫", "Chocolate|🍫; Block chocolate|🍫; Lollies|🍬; Gum; Mints; Ice blocks|🍧; Marshmallows; "
               "Jelly|🍮; Lollipops|🍭"),
    "Frozen": ("🧊", "Frozen peas|🫛; Frozen veg|🥦; Frozen corn|🌽; Hot chips|🍟; Wedges|🍟; Hash browns; "
               "Ice cream|🍨; Ice blocks|🍧; Frozen pizza|🍕; Fish fingers|🐟; Chicken nuggets|🍗; Frozen berries|🫐; "
               "Frozen mango|🥭; Dumplings|🥟; Spring rolls; Pies|🥧; Sausage rolls; Frozen meals|🍱; Ice|🧊; "
               "Puff pastry|🥐; Garlic bread|🥖"),
    "Drinks": ("🥤", "Water|💧; Sparkling water|🫧; Juice|🧃; Orange juice|🍊; Apple juice|🍎; Kids juice boxes|🧃; "
               "Soft drink|🥤; Cola|🥤; Lemonade|🍋; Cordial|🧃; Iced tea|🍹; Sports drinks; Energy drinks; "
               "Kombucha|🍾; Coconut water|🥥; Flavoured milk|🥛"),
    "Coffee & tea": ("☕", "Coffee|☕; Coffee beans|☕; Coffee pods|☕; Instant coffee|☕; Tea|🍵; Green tea|🍵; "
                     "Herbal tea|🍵; Chai|🍵; Milo|🍫; Hot chocolate|☕"),
    "Alcohol": ("🍷", "Beer|🍺; Wine|🍷; Red wine|🍷; White wine|🥂; Sparkling wine|🍾; Cider|🍏; Spirits|🥃; "
                "Pre-mixed drinks|🍹"),
    "Cleaning": ("🧽", "Dishwashing liquid|🧽; Dishwasher tablets|🫧; Rinse aid; Spray & wipe|🧴; Bathroom cleaner|🧴; "
                 "Toilet cleaner|🚽; Bleach; Glass cleaner|🪟; Floor cleaner; Disinfectant; Sponges|🧽; "
                 "Scrubbers|🧽; Cloths; Rubber gloves|🧤; Air freshener"),
    "Laundry": ("🧺", "Laundry liquid|🧺; Laundry powder|🧺; Fabric softener|🧺; Stain remover; Laundry pods|🧺; Pegs"),
    "Paper & wraps": ("🧻", "Toilet paper|🧻; Paper towel|🧻; Tissues|🤧; Serviettes; Cling wrap|📦; Foil|📦; "
                      "Baking paper; Zip-lock bags|📦; Sandwich bags; Bin bags|🗑; Freezer bags|🧊"),
    "Personal care": ("🧴", "Toothpaste|🪥; Toothbrush|🪥; Floss; Mouthwash; Shampoo|🧴; Conditioner|🧴; Soap|🧼; "
                      "Body wash|🧴; Hand wash|🧼; Deodorant; Moisturiser|🧴; Sunscreen|🧴; Razors|🪒; Shaving cream; "
                      "Cotton pads; Cotton buds; Hair ties; Lip balm; Feminine care"),
    "Health": ("💊", "Panadol|💊; Nurofen|💊; Vitamins|💊; Band-aids|🩹; Antiseptic|🧴; Cold & flu tablets|💊; "
               "Hayfever tablets|💊; Thermometer|🌡; Hand sanitiser|🧴; Insect repellent; Antihistamine|💊"),
    "Baby & kids": ("👶", "Nappies|👶; Wipes|🧻; Baby food|🍼; Formula|🍼; Nappy cream; Baby wash|🛁; "
                    "Kids toothpaste|🪥; Kids vitamins|💊"),
    "Pets": ("🐾", "Dog food|🐕; Dog treats|🦴; Cat food|🐈; Cat treats|🐈; Kitty litter|🐈; Poo bags|🐕; Bird seed|🐦; Fish food|🐠"),
    "Household": ("🏠", "Batteries|🔋; Light globes|💡; Candles|🕯; Matches; Lighters; Garbage bags|🗑; "
                  "Storage containers; Plastic cups|🥤; Paper plates|🍽; Party supplies|🎉; Gift wrap|🎁; Birthday candles|🎂"),
}


def _parse_aisles() -> dict[str, list[tuple[str, str]]]:
    out: dict[str, list[tuple[str, str]]] = {}
    for aisle, (icon, items) in _AISLES.items():
        rows = []
        for part in items.split(";"):
            name, _, emo = part.strip().partition("|")
            if name.strip():
                rows.append((name.strip(), emo.strip() or icon))
        out[aisle] = rows
    return out


PANTRY: dict[str, list[tuple[str, str]]] = _parse_aisles()
AISLE_ICONS = {aisle: icon for aisle, (icon, _) in _AISLES.items()}


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
    return {"categories": [{"name": c, "icon": AISLE_ICONS.get(c, "🛒"), "items": i} for c, i in cats.items()],
            "often": recent}


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
