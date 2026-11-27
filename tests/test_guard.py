import math
import os
from pathlib import Path

import pytest

from ghost_server import cameras, guard, store
from ghost_server.tools import Ctx, TOOLS, load

DRIVE = cameras.Cam("driveway", "Driveway", "ha", "camera.driveway")
BACK = cameras.Cam("backyard", "Backyard", "ha", "camera.backyard")


def vec(angle):
    """A unit appearance print; prints with close angles are 'the same person'."""
    v = [0.0] * 8
    v[0], v[1] = math.cos(angle), math.sin(angle)
    return v


class FakeEngine:
    def __init__(self):
        self.frames = {}

    def people(self, jpeg):
        return [{"box": [0, 0, 60, 120], "score": 0.9, "emb": e, "crop": c} for e, c in self.frames.get(jpeg, [])]


@pytest.fixture
def env(tmp_path, monkeypatch):
    store.init(tmp_path / "guard.db")
    fake = FakeEngine()
    monkeypatch.setattr(guard, "engine", fake)
    said, notified = [], []
    monkeypatch.setattr(guard, "_announce", lambda text, quiet=False, extra=None: said.append((text, extra or {})))
    monkeypatch.setattr(guard, "_ha_notify", lambda title, text: notified.append(text))
    monkeypatch.setattr(guard, "describe", lambda jpeg: "a person in a dark hoodie")
    monkeypatch.setattr(guard.watcher, "save_picture", lambda *a, **k: "/files/x")
    monkeypatch.setattr(guard.cameras, "issue", lambda cams: "tok")
    monkeypatch.setattr(guard, "_devices", lambda: [])
    monkeypatch.setattr(guard, "family_name", lambda crop: "Adam" if crop == b"adam-face" else "")
    guard.tracks.clear()
    return fake, said, notified


def test_stranger_is_reported_once_confirmed_then_followed_to_another_camera(env):
    fake, said, notified = env
    fake.frames = {b"d1": [(vec(0.0), b"")], b"d2": [(vec(0.05), b"")], b"b1": [(vec(0.08), b"")],
                   b"b2": [(vec(0.1), b"")]}
    guard.consider(DRIVE, b"d1", now=1000)
    assert said == []                                    # one frame is not enough
    guard.consider(DRIVE, b"d2", now=1002)
    assert len(said) == 1 and "Driveway" in said[0][0] and "don't recognise" in said[0][0]
    assert said[0][1]["guard"] and notified                # phones away from home hear it too
    guard.consider(DRIVE, b"d2", now=1004)
    assert len(said) == 1                                  # no repeat on the same camera
    guard.consider(BACK, b"b1", now=1030)
    assert len(said) == 2 and "same person is now at the Backyard" in said[1][0] and "Driveway" in said[1][0]
    st = guard.state()
    assert st["people"][0]["route"] == ["Driveway", "Backyard"]


def test_family_face_silences_the_track(env):
    fake, said, _ = env
    fake.frames = {b"f1": [(vec(1.0), b"adam-face")], b"f2": [(vec(1.02), b"")], b"f3": [(vec(1.03), b"")]}
    for i, f in enumerate([b"f1", b"f2", b"f3"]):
        guard.consider(DRIVE, f, now=2000 + i)
    assert said == []
    assert guard.tracks.items[0].family == "Adam"


def test_two_different_people_are_two_tracks(env):
    fake, said, _ = env
    fake.frames = {b"two": [(vec(0.0), b""), (vec(1.5), b"")]}
    guard.consider(DRIVE, b"two", now=3000)
    guard.consider(DRIVE, b"two", now=3002)
    assert len(guard.tracks.items) == 2 and len(said) == 2


def test_old_tracks_are_forgotten(env):
    fake, said, _ = env
    fake.frames = {b"a": [(vec(0.0), b"")]}
    guard.consider(DRIVE, b"a", now=4000)
    guard.consider(DRIVE, b"a", now=4002)
    guard.consider(DRIVE, b"a", now=4000 + guard.TRACK_FORGET + 10)
    assert len(guard.tracks.items) == 1 and guard.tracks.items[0].hits == 1   # a new track, not yet confirmed


def test_everyone_away_rule(env, monkeypatch):
    started, stopped = [], []
    monkeypatch.setattr(guard, "start", lambda by="", auto=False: started.append((by, auto)) or guard._status.update(on=True, auto=auto))
    monkeypatch.setattr(guard, "stop", lambda by="": stopped.append(by) or guard._status.update(on=False, auto=False))
    guard._status.update(on=False, auto=False)
    away = [{"entity_id": "person.adam", "state": "not_home"}, {"entity_id": "person.hayley", "state": "Work"}]
    home = [{"entity_id": "person.adam", "state": "home"}, {"entity_id": "person.hayley", "state": "Work"}]
    assert guard.everyone_away(away) is True and guard.everyone_away(home) is False
    assert guard.everyone_away([{"entity_id": "person.x", "state": "unknown"}]) is None
    assert guard.auto_tick(away) == "on" and started == [("Everyone left home", True)]
    assert guard.auto_tick(away) == ""
    assert guard.auto_tick(home) == "off" and stopped
    guard.save_settings({"auto_away": False})
    assert guard.auto_tick(away) == ""


def test_manual_guard_is_not_switched_off_when_someone_comes_home(env, monkeypatch):
    guard._status.update(on=True, auto=False)
    monkeypatch.setattr(guard, "stop", lambda by="": pytest.fail("must not stop a guard someone turned on"))
    assert guard.auto_tick([{"entity_id": "person.adam", "state": "home"}]) == ""
    guard._status.update(on=False)


def test_settings_only_take_known_values(env):
    s = guard.save_settings({"cameras": ["Driveway"], "ha_notify": ["mobile_app_pixel", "persistent_notification"],
                             "evil": 1})
    assert s["cameras"] == ["Driveway"] and s["ha_notify"] == ["mobile_app_pixel"] and "evil" not in s


def test_guard_tool(env, monkeypatch):
    load()
    monkeypatch.setattr(cameras, "all_cameras", lambda: [DRIVE, BACK])
    monkeypatch.setattr(guard, "_watch", lambda cam: None)
    ctx = Ctx(device_id="")
    on = TOOLS["guard_mode"].handler({"action": "on"}, ctx)
    assert "Guard mode is on" in on and "Driveway" in on
    assert "since" in TOOLS["guard_mode"].handler({"action": "status"}, ctx)
    assert store.get("guard_on")["by"] == "voice"
    assert TOOLS["guard_mode"].handler({"action": "off"}, ctx) == "Guard mode is off."
    assert store.get("guard_on") is None


ZOO = Path("/tmp/claude-0/zoo")


@pytest.mark.skipif(not (ZOO / "bus.jpg").exists(), reason="real models and pictures not on this machine")
def test_real_models_find_people_and_tell_them_apart(monkeypatch):
    monkeypatch.setenv("GHOST_MODELS", str(ZOO))
    eng = guard.Engine()
    people = eng.people((ZOO / "bus.jpg").read_bytes())
    assert len(people) >= 3
    a, b = people[0]["emb"], people[1]["emb"]
    assert guard._cos(a, a) > 0.99 and guard._cos(a, b) < guard.SAME_PERSON
