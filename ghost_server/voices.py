"""Opt-in household speaker identification, never an authentication mechanism.

Only normalized embeddings are retained. PCM is processed on this server and
discarded; unrecognised speakers are never enrolled implicitly.
"""
from __future__ import annotations
import base64
import hashlib
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
import numpy as np
from . import config, store

MODEL = "wespeaker_en_voxceleb_resnet34_LM.onnx"
MODEL_SHA = "e9848563da86f263117134dfd7ad63c92355b37de492b55e325400c9d9c39012"
RATE = 16000
THRESHOLD = .90
MARGIN = .08
PHRASES = [
    "Hello Ghost. This is my voice, and I am speaking naturally.",
    "Please tell me what is happening around the house today.",
    "I would like to check the weather before I go outside.",
    "Thank you Ghost. Let us make a plan for the rest of the day.",
]
SCHEMA = """CREATE TABLE IF NOT EXISTS voice_profiles (
 person_id INTEGER NOT NULL, device_id TEXT NOT NULL, model TEXT NOT NULL, embedding BLOB NOT NULL,
 samples INTEGER NOT NULL, updated_at REAL NOT NULL, PRIMARY KEY(person_id,device_id));"""
_lock = threading.RLock()


def _schema():
    store._con().executescript(SCHEMA)


def _model_path():
    return Path(os.environ.get("GHOST_MODELS", str(config.DATA_DIR / "models"))) / MODEL


def unit(vector):
    v = np.asarray(vector, dtype=np.float32)
    if v.ndim != 1 or not 32 <= v.size <= 1024 or not np.isfinite(v).all():
        raise ValueError("The speaker model returned an invalid voice print")
    length = float(np.linalg.norm(v))
    if length < 1e-8:
        raise ValueError("The speaker model returned an empty voice print")
    return v / length


class Engine:
    def __init__(self):
        self._extractor = None
        self._lock = threading.Lock()

    def ready(self):
        with self._lock:
            if self._extractor is None:
                import sherpa_onnx
                path = _model_path()
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != MODEL_SHA:
                    raise RuntimeError("Speaker model missing or checksum mismatch. Rebuild the Ghost Server image.")
                cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(path), num_threads=2, provider="cpu")
                if not cfg.validate():
                    raise RuntimeError("Speaker model configuration is invalid")
                self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)

    def extract(self, samples):
        self.ready()
        with self._lock:
            stream = self._extractor.create_stream()
            stream.accept_waveform(sample_rate=RATE, waveform=np.ascontiguousarray(samples))
            stream.input_finished()
            if not self._extractor.is_ready(stream):
                raise ValueError("Please speak a little longer")
            return unit(self._extractor.compute(stream))


engine = Engine()


def decode_audio(encoded: str):
    if not isinstance(encoded, str) or len(encoded) > 520_000:
        raise ValueError("Audio clip is too large")
    try:
        pcm = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError("Invalid PCM audio") from exc
    if len(pcm) % 2 or not RATE * 2 * 2 <= len(pcm) <= RATE * 2 * 12:
        raise ValueError("Use 2–12 seconds of 16 kHz mono PCM audio")
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.
    if float(np.mean(np.abs(samples) > .98)) > .03:
        raise ValueError("That recording is distorted. Move slightly farther from the microphone.")
    frames = samples[:len(samples) // 320 * 320].reshape(-1, 320)
    energy = np.sqrt(np.mean(frames ** 2, axis=1))
    floor = max(.008, float(np.percentile(energy, 20)) * 2)
    if float(np.sum(energy > floor)) * .02 < 1.0:
        raise ValueError("Not enough clear speech. Try again in a quiet room and speak normally.")
    return samples, hashlib.sha256(pcm).hexdigest()


@dataclass
class Enrollment:
    device: str
    person_id: int
    expires: float
    vectors: list = field(default_factory=list)
    hashes: list = field(default_factory=list)
    complete: bool = False


_enrollments: dict[str, Enrollment] = {}
_last_query: dict[str, float] = {}


def ticket(device: str, person_id: int) -> str:
    with _lock:
        now = time.time()
        for key in [k for k, v in _enrollments.items() if v.expires <= now]:
            del _enrollments[key]
        if len(_enrollments) >= 128:
            raise ValueError("Too many pending voice enrolments. Try again later.")
        key = secrets.token_urlsafe(24)
        _enrollments[key] = Enrollment(device, person_id, now + 600)
        return key


def _held(token: str, device: str) -> Enrollment:
    held = _enrollments.get(token)
    if held is None or held.device != device or held.expires <= time.time():
        raise PermissionError("Voice enrolment expired. Ask the owner to start it again.")
    if not store.q("SELECT id FROM people WHERE id=?", (held.person_id,)):
        raise ValueError("That person no longer exists")
    return held


def _progress(token, held):
    return {"ok": True, "ticket": token, "phrases": PHRASES,
            "samples": len(held.hashes), "complete": held.complete, "model": MODEL}


def begin(token: str, device: str, name: str):
    with _lock:
        held = _held(token, device)
        rows = store.q("SELECT name FROM people WHERE id=?", (held.person_id,))
        if str(rows[0]["name"]).casefold() != name.strip().casefold():
            raise PermissionError("That voice ticket belongs to a different person")
    engine.ready()
    return _progress(token, held)


def enroll(token: str, device: str, index: int, encoded: str):
    with _lock:
        held = _held(token, device)
        if not 0 <= index < len(PHRASES):
            raise ValueError("Invalid phrase number")
    samples, digest = decode_audio(encoded)
    with _lock:
        if index < len(held.hashes) and digest == held.hashes[index]:
            return _progress(token, held)  # safe network retry
        if held.complete or index != len(held.hashes) or digest in held.hashes:
            raise ValueError("Please record the current phrase once more; do not reuse a recording.")
    vector = unit(engine.extract(samples))
    with _lock:
        held = _held(token, device)
        if index != len(held.hashes):
            raise ValueError("A recording was already accepted. Refresh the enrolment progress.")
        if held.vectors:
            center = unit(np.mean(held.vectors, axis=0))
            if center.shape != vector.shape or float(center @ vector) < .88:
                raise ValueError("That voice did not match the earlier phrases. Use the same speaker in a quiet room.")
        held.vectors.append(vector)
        held.hashes.append(digest)
        if len(held.vectors) == len(PHRASES):
            _schema()
            center = unit(np.mean(held.vectors, axis=0))
            store.x("INSERT INTO voice_profiles VALUES(?,?,?,?,?,?) ON CONFLICT(person_id,device_id) DO UPDATE SET "
                    "model=excluded.model,embedding=excluded.embedding,samples=excluded.samples,updated_at=excluded.updated_at",
                    (held.person_id, held.device, MODEL, center.astype("<f4").tobytes(), len(PHRASES), time.time()))
            held.complete = True
            held.vectors.clear()
        return _progress(token, held)


def profiles():
    _schema()
    return [dict(r) for r in store.q("SELECT person_id,device_id,model,samples,updated_at FROM voice_profiles")]


def delete(person_id: int):
    with _lock:
        _schema()
        store.x("DELETE FROM voice_profiles WHERE person_id=?", (person_id,))
        for key in [k for k, v in _enrollments.items() if v.person_id == person_id]:
            del _enrollments[key]


def match(vector, rows):
    vector = unit(vector)
    best = {}
    for row in rows:
        stored = np.frombuffer(row["embedding"], dtype="<f4")
        if stored.shape == vector.shape:
            key = row.get("person_id", row["name"].casefold()) if isinstance(row, dict) else row["person_id"]
            score = float(unit(stored) @ vector)
            if key not in best or score > best[key][0]:
                best[key] = (score, row)
    scores = list(best.values())
    scores.sort(key=lambda item: item[0], reverse=True)
    if not scores or scores[0][0] < THRESHOLD:
        return {"person": None, "reason": "unknown"}
    if len(scores) > 1 and scores[0][0] - scores[1][0] < MARGIN:
        return {"person": None, "reason": "ambiguous"}
    score, row = scores[0]
    return {"person": row["name"], "score": round(score, 3), "personalisation_only": True}


def identify(encoded: str, device: str):
    _schema()
    rows = store.q("SELECT v.*,p.name FROM voice_profiles v JOIN people p ON p.id=v.person_id WHERE v.model=?", (MODEL,))
    if not rows:
        return {"person": None, "reason": "not_enrolled"}
    with _lock:
        now = time.monotonic()
        if now - _last_query.get(device, -100) < 8:
            return {"person": None, "reason": "try_later"}
        if len(_last_query) > 512:
            _last_query.clear()
        _last_query[device] = now
    samples, _ = decode_audio(encoded)
    return match(engine.extract(samples), rows)
