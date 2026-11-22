import base64
import struct
import zlib

import pytest

from ghost_server import household, ink, store


def png(w=4, h=3) -> bytes:
    raw = b"".join(b"\x00" + b"\xff\x00\x00\xff" * w for _ in range(h))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return (ink.PNG_MAGIC + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


B64 = base64.b64encode(png()).decode()


@pytest.fixture
def env(tmp_path, monkeypatch):
    store.init(tmp_path / "ink.db")
    monkeypatch.setattr(household, "_ready", False)
    seen = []
    monkeypatch.setattr(ink, "read_handwriting", lambda data, mode="note": seen.append(mode) or (
        "milk\n- eggs\n2. bread\nmilk" if mode == "list" else "Pizza night Friday"))
    return seen


def test_decode_png_rejects_bad_input():
    assert ink.decode_png("data:image/png;base64," + B64) == png()
    for bad in ("not base64!!", base64.b64encode(b"GIF89a....").decode(), ""):
        with pytest.raises(ValueError):
            ink.decode_png(bad)
    with pytest.raises(ValueError):
        ink.decode_png(base64.b64encode(ink.PNG_MAGIC + b"0" * (ink.MAX_PNG + 1)).decode())


def test_ink_names_cannot_escape_the_folder(env):
    name = ink.save_ink(png())
    assert ink.ink_path(name).read_bytes() == png()
    for bad in ("../ghost.db", "x.png", name.replace(".png", ".db"), "a/" + name):
        assert ink.ink_path(bad) is None


def test_list_items_cleanup():
    assert ink.list_items("Milk\n- eggs\n• Bread, 2. butter\nmilk\n\n") == ["Milk", "eggs", "Bread", "butter"]


def test_pantry_catalogue_custom_items_and_often_bought(env):
    p = ink.pantry()
    names = [c["name"] for c in p["categories"]]
    assert "Fruit" in names and "Household" in names
    fruit = next(c for c in p["categories"] if c["name"] == "Fruit")["items"]
    assert {"name": "Bananas", "emoji": "🍌"} in fruit
    ink.add_custom("Weet-Bix", "Pantry", "🥣"); ink.add_custom("weet-bix")       # no duplicates
    pantry_items = next(c for c in ink.pantry()["categories"] if c["name"] == "Pantry")["items"]
    assert [i["name"] for i in pantry_items].count("Weet-Bix") == 1
    with pytest.raises(ValueError):
        ink.add_custom("  ")
    for _ in range(3):
        household.add_items("shopping", "Milk")
        household.set_item(household.lists()["shopping"][-1]["id"], True)
    household.add_items("shopping", "Bananas")
    often = ink.pantry()["often"]
    assert often[0]["name"].lower() == "milk" and often[0]["emoji"] == "🥛"


def test_lists_carry_aisle_and_picture(env):
    household.add_items("shopping", ["Bananas", "Weird thing"])
    rows = {r["text"]: r for r in household.lists()["shopping"]}
    assert rows["Bananas"]["category"] == "Fruit" and rows["Bananas"]["emoji"] == "🍌"
    assert rows["Weird thing"]["category"] == "" and rows["Weird thing"]["emoji"] == ""


def test_routes(env):
    from fastapi.testclient import TestClient
    from ghost_server import security
    from ghost_server.app import app
    security.reset_all(); store.put("approve_new_devices", False)
    _, key = store.redeem_pair_code(store.new_pair_code(), "Kitchen hub", "tablet")
    h = {"Authorization": "Bearer " + key}
    c = TestClient(app)
    # handwritten note: drawing kept, Ghost reads it for the text
    r = c.post("/api/v1/notes", json={"ink": B64, "to": "Zoey", "from": "Adam", "color": "pink"}, headers=h)
    assert r.status_code == 200
    note = c.get("/api/v1/notes", headers=h).json()["items"][0]
    assert note["text"] == "Pizza night Friday" and note["color"] == "pink" and note["ink"].endswith(".png")
    img = c.get(f"/api/v1/notes/ink/{note['ink']}", headers=h)
    assert img.status_code == 200 and img.content == png()
    for bad in ("ghost.db", "short.png", "%2e%2e%2fghost.db"):
        assert c.get(f"/api/v1/notes/ink/{bad}", headers=h).status_code == 404
    assert c.get(f"/api/v1/notes/ink/{note['ink']}").status_code == 401
    # typed note keeps its own words; unknown colours are dropped
    c.post("/api/v1/notes", json={"text": "Back at 6", "color": "tartan"}, headers=h)
    typed = c.get("/api/v1/notes", headers=h).json()["items"][0]
    assert typed["text"] == "Back at 6" and typed["color"] == "" and typed["ink"] == ""
    assert c.post("/api/v1/notes", json={"text": " "}, headers=h).status_code == 400
    assert c.post("/api/v1/notes", json={"ink": "nope"}, headers=h).status_code == 400
    # done removes the drawing
    c.post(f"/api/v1/notes/{note['id']}", json={}, headers=h)
    assert ink.ink_path(note["ink"]) is None
    # handwriting for the shopping list
    hw = c.post("/api/v1/handwriting", json={"png": B64, "mode": "list"}, headers=h).json()
    assert hw["items"] == ["milk", "eggs", "bread"]
    # pantry
    assert c.get("/api/v1/pantry", headers=h).json()["categories"]
    assert c.post("/api/v1/pantry", json={"name": "Tim Tams", "category": "Snacks", "emoji": "🍫"}, headers=h).json()["name"] == "Tim Tams"


def test_unreadable_handwriting_says_so(env, monkeypatch):
    from fastapi.testclient import TestClient
    from ghost_server import security
    from ghost_server.app import app
    monkeypatch.setattr(ink, "read_handwriting", lambda data, mode="note": "")
    security.reset_all(); store.put("approve_new_devices", False)
    _, key = store.redeem_pair_code(store.new_pair_code(), "Kitchen hub", "tablet")
    c = TestClient(app)
    assert c.post("/api/v1/handwriting", json={"png": B64}, headers={"Authorization": "Bearer " + key}).status_code == 503
    # a note whose drawing can't be read is still kept
    r = c.post("/api/v1/notes", json={"ink": B64}, headers={"Authorization": "Bearer " + key})
    assert r.status_code == 200
    assert household.notes()[0]["text"] == "Handwritten note"
