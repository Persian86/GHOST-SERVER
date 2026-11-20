"""The firmware's server-address and pairing-code rules, compiled natively."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def test_server_address_and_code_rules(tmp_path):
    cc = shutil.which("cc")
    if sys.platform != "linux" or not cc:
        pytest.skip("Native firmware harness runs on Linux CI")
    root = Path(__file__).resolve().parents[1]
    main = root / "firmware/waveshare-4b/main"
    binary = tmp_path / "rules-test"
    subprocess.run([cc, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-D_DEFAULT_SOURCE",
                    "-I", str(main), str(main / "ghost_rules.c"),
                    str(root / "tests/waveshare_rules_harness.c"), "-o", str(binary)],
                   check=True, timeout=30)
    out = subprocess.run([str(binary)], check=True, timeout=30, capture_output=True, text=True)
    assert "rules ok" in out.stdout
