"""Leaks that must not happen: camera passwords in errors, device keys in the access log."""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ghost_server import cameras  # noqa: E402


def test_redact_hides_camera_credentials():
    assert cameras.redact("rtsp://admin:hunter2@10.0.0.5:554/Preview_01_sub") == "rtsp://***@10.0.0.5:554/Preview_01_sub"
    msg = "Command '['ffmpeg', '-i', 'rtsp://u:p%40ss@cam/1']' timed out; http://x:y@h/z"
    out = cameras.redact(msg)
    assert "hunter2" not in out and "p%40ss" not in out and "x:y" not in out
    assert cameras.redact("rtsp://10.0.0.5/stream") == "rtsp://10.0.0.5/stream"     # nothing to hide
    assert cameras.redact("") == ""


def test_snapshot_timeout_does_not_repeat_the_command(monkeypatch):
    def slow(args, **kw):
        raise subprocess.TimeoutExpired(args, kw.get("timeout"))
    monkeypatch.setattr(subprocess, "run", slow)
    cam = cameras.Cam(id="rtsp:gate", name="Gate", source="rtsp", url="rtsp://admin:hunter2@10.0.0.5/s")
    with pytest.raises(RuntimeError) as e:
        cameras.snapshot(cam, timeout=1)
    assert "hunter2" not in str(e.value) and "didn't answer" in str(e.value)


def test_tool_errors_are_redacted(monkeypatch):
    from ghost_server import tools
    tools.load()
    from ghost_server.tools import Ctx, TOOLS
    name = next(iter(TOOLS))
    orig = TOOLS[name].handler
    def boom(args, ctx):
        raise RuntimeError("ffmpeg failed for rtsp://admin:hunter2@10.0.0.5/s")
    monkeypatch.setattr(TOOLS[name], "handler", boom)
    monkeypatch.setattr("ghost_server.access.tool_allowed", lambda *a: True)
    out = tools.run(name, {}, Ctx("dev", "Dev", "chat", {}))
    assert "hunter2" not in out and "rtsp://***@" in out
    TOOLS[name].handler = orig


def test_access_log_masks_device_keys():
    from ghost_server import app as app_module
    f = app_module._RedactKeys()
    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                            ("1.2.3.4:5", "GET", "/files/abc?k=SECRETKEY123&x=1", "1.1", 200), None)
    f.filter(rec)
    line = rec.getMessage()
    assert "SECRETKEY123" not in line and "?k=***&x=1" in line
    rec = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1, '%s - "WebSocket %s" [accepted]',
                            ("1.2.3.4:5", "/api/v1/agent?key=LIVEKEY9876"), None)
    f.filter(rec)
    assert "LIVEKEY9876" not in rec.getMessage()
    # Everything else is left alone.
    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                            ("1.2.3.4:5", "GET", "/api/v1/health?monkey=1", "1.1", 200), None)
    f.filter(rec)
    assert "/api/v1/health?monkey=1" in rec.getMessage()
    assert any(isinstance(x, app_module._RedactKeys) for x in logging.getLogger("uvicorn.access").filters)
