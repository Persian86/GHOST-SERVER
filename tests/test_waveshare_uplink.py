"""Run real C queue ownership/order tests on the Linux CI host."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def test_bounded_audio_queue_preserves_audio_and_stop_across_threads(tmp_path):
    cc = shutil.which("cc")
    if sys.platform != "linux" or not cc:
        pytest.skip("Native C11 threads harness runs on Linux CI")
    root = Path(__file__).resolve().parents[1]
    main = root / "firmware/waveshare-4b/main"
    binary = tmp_path / "uplink-test"
    subprocess.run([cc, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                    "-Wno-unused-parameter", "-pthread", "-I", str(main),
                    str(main / "ghost_uplink.c"), str(root / "tests/waveshare_uplink_harness.c"),
                    "-o", str(binary)], check=True, timeout=30)
    subprocess.run([str(binary)], check=True, timeout=30)
