import base64
import sys
import time
from pathlib import Path
import numpy as np
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ghost_server import store, faces, voices


@pytest.fixture
def setup(tmp_path, monkeypatch):
    store.init(tmp_path / "voices.db")
    faces._ready = False
    faces._schema()
    voices._enrollments.clear()
    voices._last_query.clear()
    pid = store.x("INSERT INTO people(name,created_at) VALUES (?,?)", ("Alice", time.time()))
    class FakeEngine:
        def ready(self): pass
        def extract(self, samples):
            return np.array([1.] + [0.] * 31, dtype=np.float32)
    monkeypatch.setattr(voices, "engine", FakeEngine())
    return pid


def pcm(seed=0):
    rng = np.random.default_rng(seed)
    speech = np.sin(np.arange(32000) * .08) * .1 + rng.normal(0, .001, 32000)
    samples = np.concatenate((np.zeros(16000), speech, np.zeros(16000)))
    return base64.b64encode((samples * 32767).astype("<i2").tobytes()).decode()


def test_enrolment_is_bound_to_device_and_person(setup):
    key = voices.ticket("tablet", setup)
    with pytest.raises(PermissionError): voices.begin(key, "phone", "Alice")
    with pytest.raises(PermissionError): voices.begin(key, "tablet", "Bob")
    assert len(voices.begin(key, "tablet", "alice")["phrases"]) == 4


def test_profile_published_only_after_four_samples_and_retry_is_safe(setup):
    key = voices.ticket("tablet", setup)
    for i in range(4):
        result = voices.enroll(key, "tablet", i, pcm(i))
        assert result["samples"] == i + 1
        assert bool(voices.profiles()) == (i == 3)
    assert voices.enroll(key, "tablet", 3, pcm(3))["complete"]
    row = store.q("SELECT * FROM voice_profiles")[0]
    assert len(row["embedding"]) == 32 * 4
    assert "pcm" not in row.keys()


def test_duplicate_audio_and_out_of_order_are_rejected(setup):
    key = voices.ticket("tablet", setup)
    with pytest.raises(ValueError): voices.enroll(key, "tablet", 1, pcm())
    voices.enroll(key, "tablet", 0, pcm())
    with pytest.raises(ValueError): voices.enroll(key, "tablet", 1, pcm())


def test_silence_distortion_invalid_and_oversize_fail_closed():
    for encoded in ("not base64", "A" * 520001,
                    base64.b64encode(bytes(16000 * 6)).decode(),
                    base64.b64encode(np.full(48000, 32767, dtype="<i2").tobytes()).decode()):
        with pytest.raises(ValueError): voices.decode_audio(encoded)


def test_unknown_ambiguous_and_known_matches():
    def row(name, vector): return {"name": name, "embedding": voices.unit(vector).astype("<f4").tobytes()}
    a = [1.] + [0.] * 31
    b = [0., 1.] + [0.] * 30
    assert voices.match(a, [row("Alice", a), row("Bob", b)])["person"] == "Alice"
    assert voices.match(a, [row("Bob", b)])["person"] is None
    assert voices.match(a, [row("Alice", a), row("Bob", a)])["reason"] == "ambiguous"


def test_deleting_person_removes_voice_and_pending_enrolment(setup):
    key = voices.ticket("tablet", setup)
    for i in range(4): voices.enroll(key, "tablet", i, pcm(i))
    faces.delete_person(setup)
    assert voices.profiles() == []
    with pytest.raises(PermissionError): voices.begin(key, "tablet", "Alice")


def test_expired_enrolment_and_mixed_speakers_rejected(setup, monkeypatch):
    key = voices.ticket("tablet", setup)
    voices._enrollments[key].expires = 0
    with pytest.raises(PermissionError): voices.begin(key, "tablet", "Alice")
    key = voices.ticket("tablet", setup)
    voices.enroll(key, "tablet", 0, pcm())
    monkeypatch.setattr(voices.engine, "extract", lambda _: np.array([0., 1.] + [0.] * 30))
    with pytest.raises(ValueError, match="same speaker"):
        voices.enroll(key, "tablet", 1, pcm(1))
    assert voices.profiles() == []


def test_device_profiles_coexist_and_do_not_compete_as_two_people(setup):
    for device in ("tablet", "phone"):
        key = voices.ticket(device, setup)
        for i in range(4): voices.enroll(key, device, i, pcm(i))
    assert len(voices.profiles()) == 2
    rows = store.q("SELECT v.*,p.name FROM voice_profiles v JOIN people p ON p.id=v.person_id")
    assert voices.match([1.] + [0.] * 31, rows)["person"] == "Alice"


def test_http_requires_pairing_owner_and_bound_ticket(setup, monkeypatch):
    from fastapi.testclient import TestClient
    from ghost_server.app import app, device_auth, admin_auth
    import ghost_server.app as api
    client = TestClient(app)
    assert client.post('/api/v1/voices/enroll/start', json={"name": "Alice"}).status_code == 401
    app.dependency_overrides[device_auth] = lambda: {"id": "tablet"}
    def deny(*_):
        from fastapi import HTTPException
        raise HTTPException(403, "Owner approval required")
    monkeypatch.setattr(api, '_admin_password_check', deny)
    try:
        assert client.post('/api/v1/voices/enroll/start', json={"name": "Alice"}).status_code == 403
        other = voices.ticket("phone", setup)
        assert client.post('/api/v1/voices/enroll/start', json={"name": "Alice", "ticket": other}).status_code == 403
        token = voices.ticket("tablet", setup)
        assert client.post('/api/v1/voices/enroll/start', json={"name": "Alice", "ticket": token}).status_code == 200
        for i in range(4):
            response = client.post('/api/v1/voices/enroll/sample', json={"ticket": token, "index": i, "pcm": pcm(i), "sample_rate": 16000})
            assert response.status_code == 200
        assert client.post('/api/v1/voices/identify', json={"pcm": pcm(10)}).json()["person"] == "Alice"
        assert client.post(f'/admin/api/people/{setup}/voice/delete').status_code == 401
        app.dependency_overrides[admin_auth] = lambda: True
        assert client.post(f'/admin/api/people/{setup}/voice/delete').status_code == 200
        assert voices.profiles() == []
    finally:
        app.dependency_overrides.clear()
