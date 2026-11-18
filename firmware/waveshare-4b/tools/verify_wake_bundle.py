"""Validate the actual ESP-SR bundle before embedding it in the application."""
import struct
import sys
from pathlib import Path


def validate(data: bytes) -> dict[str, set[str]]:
    def number(offset):
        if offset < 0 or offset + 4 > len(data):
            raise ValueError("Truncated bundle header")
        return struct.unpack_from("<I", data, offset)[0]

    def name(offset):
        raw = data[offset:offset + 32]
        if len(raw) != 32 or b"\0" not in raw:
            raise ValueError("Invalid bundle name")
        value = raw.split(b"\0", 1)[0].decode("ascii")
        if not value or "/" in value or "\\" in value:
            raise ValueError("Invalid bundle name")
        return value

    count = number(0)
    if not 1 <= count <= 16:
        raise ValueError("Invalid model count")
    position = 4
    models = {}
    ranges = []
    for _ in range(count):
        model = name(position)
        files = number(position + 32)
        if model in models or not 1 <= files <= 32:
            raise ValueError("Duplicate model or invalid file count")
        position += 36
        models[model] = set()
        for _ in range(files):
            filename = name(position)
            offset, size = number(position + 32), number(position + 36)
            if filename in models[model] or size == 0 or offset + size > len(data):
                raise ValueError("Duplicate file or invalid payload bounds")
            models[model].add(filename)
            ranges.append((offset, offset + size))
            position += 40
    end = position
    for start, stop in sorted(ranges):
        if start < end:
            raise ValueError("Overlapping bundle payload")
        end = stop
    required = {"mn7_en": {"_MODEL_INFO_", "mn7_index", "mn7_data", "vocab", "LICENSE"},
                "fst": {"commands_en.txt"}}
    if any(not files <= models.get(model, set()) for model, files in required.items()):
        raise ValueError("Missing English model, vocabulary, license or fst command dictionary")
    return models


if __name__ == "__main__":
    result = validate(Path(sys.argv[1]).read_bytes())
    print("Validated embedded English model and fst command dictionary:", sorted(result))
