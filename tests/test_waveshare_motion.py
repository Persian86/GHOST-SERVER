"""Exercise the real firmware motion math, independently of the display driver."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def test_motion_bounds_easing_and_delayed_frame_recovery(tmp_path):
    cc = shutil.which("cc")
    if sys.platform != "linux" or not cc:
        pytest.skip("Native motion harness runs on Linux CI")
    root = Path(__file__).resolve().parents[1]
    main = root / "firmware/waveshare-4b/main"
    binary = tmp_path / "motion-test"
    subprocess.run([cc, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                    "-I", str(main), str(main / "ghost_motion.c"),
                    str(root / "tests/waveshare_motion_harness.c"), "-lm", "-o", str(binary)],
                   check=True, timeout=30)
    subprocess.run([str(binary)], check=True, timeout=30)
