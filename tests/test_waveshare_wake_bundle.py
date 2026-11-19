"""Regression coverage for the missing fst bundle that caused a hardware panic."""
import runpy
import struct
from pathlib import Path

import pytest

validate = runpy.run_path(str(Path(__file__).resolve().parents[1] /
    "firmware/waveshare-4b/tools/verify_wake_bundle.py"))["validate"]


def bundle(include_fst=True):
    models = {"mn7_en": ["_MODEL_INFO_", "mn7_index", "mn7_data", "vocab", "LICENSE"]}
    if include_fst:
        models["fst"] = ["commands_en.txt"]
    size = 4 + sum(36 + 40 * len(files) for files in models.values())
    header = bytearray(struct.pack("<I", len(models)))
    payload = bytearray()
    for model, files in models.items():
        header += struct.pack("<32sI", model.encode(), len(files))
        for filename in files:
            content = (filename + "\n").encode()
            header += struct.pack("<32sII", filename.encode(), size + len(payload), len(content))
            payload += content
    return bytes(header + payload)


def test_requires_separate_command_dictionary_even_when_vocabulary_exists():
    with pytest.raises(ValueError, match="fst command dictionary"):
        validate(bundle(include_fst=False))
    assert "fst" in validate(bundle())


def test_payload_cannot_point_into_header_or_outside_bundle():
    data = bytearray(bundle())
    struct.pack_into("<I", data, 40 + 32, 0)
    with pytest.raises(ValueError, match="Overlapping"):
        validate(data)
    struct.pack_into("<I", data, 40 + 32, len(data) + 1)
    with pytest.raises(ValueError, match="bounds"):
        validate(data)


def test_truncated_header_is_rejected():
    with pytest.raises(ValueError):
        validate(bundle()[:45])
