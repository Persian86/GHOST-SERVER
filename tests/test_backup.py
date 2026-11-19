"""Settings backup file: password-locked, tamper-evident, version-tolerant.
(The admin endpoints are tested with the live server in test_server.py.)"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ghost_server import backup  # noqa: E402

SETTINGS = {"anthropic_api_key": "sk-ant-real-key-9876", "ha_token": "ha-secret", "city": "Melbourne"}


@pytest.fixture
def saved(monkeypatch):
    monkeypatch.setattr(backup, "stored_settings", lambda: dict(SETTINGS))


def test_round_trip(saved):
    text, n = backup.export("correct horse")
    assert n == 3
    assert "sk-ant-real-key-9876" not in text and "ha-secret" not in text
    assert json.loads(text)["format"] == "ghost-server-settings"
    assert backup.read(text, "correct horse") == SETTINGS


def test_short_password_refused(saved):
    with pytest.raises(backup.BackupError, match="8 characters"):
        backup.export("short")


def test_wrong_password_and_tampering_are_caught(saved):
    text, _ = backup.export("correct horse")
    with pytest.raises(backup.BackupError, match="Wrong backup password"):
        backup.read(text, "wrong horse")
    doc = json.loads(text)
    data = bytearray(base64.b64decode(doc["data"]))
    data[5] ^= 1
    doc["data"] = base64.b64encode(bytes(data)).decode()
    with pytest.raises(backup.BackupError, match="Wrong backup password|changed"):
        backup.read(json.dumps(doc), "correct horse")
    weak = json.loads(text); weak["kdf"]["n"] = 2            # weakened KDF: refused outright
    with pytest.raises(backup.BackupError, match="damaged"):
        backup.read(json.dumps(weak), "correct horse")
    other = json.loads(text); other["kdf"]["n"] = 2 ** 14    # header is authenticated too
    with pytest.raises(backup.BackupError):
        backup.read(json.dumps(other), "correct horse")


def test_not_a_backup(saved):
    for junk in ('{"hello": 1}', "not json", "[]"):
        with pytest.raises(backup.BackupError, match="isn't a Ghost"):
            backup.read(junk, "x")
    with pytest.raises(backup.BackupError, match="too big"):
        backup.read("x" * (backup.MAX_FILE_BYTES + 1), "x")


def test_newer_format_asks_for_an_update(saved):
    doc = json.loads(backup.export("correct horse")[0]); doc["version"] = 99
    with pytest.raises(backup.BackupError, match="newer"):
        backup.read(json.dumps(doc), "correct horse")


def test_unknown_and_sign_in_keys_are_skipped():
    usable, skipped = backup.plan({"city": "Hobart", "future_setting": 1, "admin_password_hash": "evil"})
    assert usable == {"city": "Hobart"}
    assert skipped == ["admin_password_hash", "future_setting"]
