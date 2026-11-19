"""The offline backup brain: Ollama translation, and Claude → local failover."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import pytest

from ghost_server import local_llm
from ghost_server.claude import FunctionCall
from ghost_server.local_llm import OllamaLLM, ResilientLLM, to_ollama_messages, to_ollama_tools


def fake_ollama(reply: dict, seen: list):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3.5:9b"}]})
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"message": reply})
    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def fresh_state():
    local_llm._state.update(offline_until=0.0, last_error="", last_used="")
    yield


HISTORY = [
    {"role": "system", "content": "You are Ghost."},
    {"role": "user", "content": "turn off the lounge lights"},
    {"role": "assistant", "content": None, "tool_calls": [
        {"id": "toolu_1", "type": "function", "function": {"name": "home_control", "arguments": json.dumps({"entity": "light.lounge", "action": "off"})}}]},
    {"role": "tool", "tool_call_id": "toolu_1", "content": "done"},
]


def test_history_translates_to_ollama():
    out = to_ollama_messages(HISTORY + [{"role": "user", "content": [
        {"type": "text", "text": "what's this?"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}}]}])
    assert out[0] == {"role": "system", "content": "You are Ghost."}
    assert out[2]["tool_calls"] == [{"function": {"name": "home_control", "arguments": {"entity": "light.lounge", "action": "off"}}}]
    assert out[3] == {"role": "tool", "content": "done", "tool_name": "home_control"}
    assert out[4] == {"role": "user", "content": "what's this?", "images": ["QUJD"]}


def test_tools_translate_from_claude_shape():
    t = to_ollama_tools([{"name": "timer", "description": "set a timer", "input_schema": {"type": "object", "properties": {"minutes": {"type": "integer"}}}}])
    assert t == [{"type": "function", "function": {"name": "timer", "description": "set a timer",
                  "parameters": {"type": "object", "properties": {"minutes": {"type": "integer"}}}}}]


def test_local_model_returns_text_and_tool_calls():
    seen = []
    llm = OllamaLLM({"url": "192.168.80.165:11434", "model": "qwen3.5:9b"}, transport=fake_ollama(
        {"content": "<think>hmm</think>Okay.", "tool_calls": [{"function": {"name": "timer", "arguments": {"minutes": 5}}}]}, seen))
    assert llm.base == "http://192.168.80.165:11434"
    text, calls = llm.complete(HISTORY, tools=[{"name": "timer", "description": "", "input_schema": {}}], max_tokens=50)
    assert text == "Okay."
    assert [(c.name, c.args) for c in calls] == [("timer", {"minutes": 5})]
    assert seen[0]["think"] is False and seen[0]["stream"] is False
    assert seen[0]["options"]["num_predict"] == 50
    assert seen[0]["tools"][0]["function"]["name"] == "timer"


class Down:
    calls = 0
    def complete(self, *a, **k):
        Down.calls += 1
        raise RuntimeError("ConnectError: [Errno 101] Network is unreachable")


class Up:
    def complete(self, *a, **k):
        return "from claude", []


class Rejects:
    def complete(self, *a, **k):
        raise RuntimeError("400 messages: roles must alternate")


def local_ok():
    return OllamaLLM({"url": "http://x"}, transport=fake_ollama({"content": "from local"}, []))


def test_claude_is_used_when_it_works():
    llm = ResilientLLM(Up(), local_ok())
    assert llm.complete(HISTORY)[0] == "from claude"
    assert llm.last_brain == "claude"


def test_falls_back_when_claude_is_unreachable_then_skips_it_for_a_while():
    Down.calls = 0
    llm = ResilientLLM(Down(), local_ok())
    assert llm.complete(HISTORY)[0] == "from local"
    assert local_llm.status()["claude_unreachable"]
    # Next request goes straight to the local model — no waiting on a dead connection.
    assert ResilientLLM(Down(), local_ok()).complete(HISTORY)[0] == "from local"
    assert Down.calls == 1


def test_claude_is_tried_again_after_the_cooldown():
    ResilientLLM(Down(), local_ok()).complete(HISTORY)
    local_llm._state["offline_until"] = 0.0
    llm = ResilientLLM(Up(), local_ok())
    assert llm.complete(HISTORY)[0] == "from claude"
    assert not local_llm.status()["claude_unreachable"]


def test_a_malformed_request_is_not_hidden_by_the_fallback():
    with pytest.raises(RuntimeError, match="400"):
        ResilientLLM(Rejects(), local_ok()).complete(HISTORY)


def test_modes():
    assert ResilientLLM(Up(), local_ok(), "only").complete(HISTORY)[0] == "from local"
    with pytest.raises(RuntimeError, match="Network"):
        ResilientLLM(Down(), local_ok(), "off").complete(HISTORY)
    # No Anthropic key at all: the local model still answers.
    assert ResilientLLM(None, local_ok()).complete(HISTORY)[0] == "from local"


def test_both_down_says_so():
    dead = OllamaLLM({"url": "http://x"}, transport=httpx.MockTransport(lambda r: httpx.Response(500, text="no gpu")))
    with pytest.raises(RuntimeError, match="and so did the local model"):
        ResilientLLM(Down(), dead).complete(HISTORY)


def test_probe_reports_a_missing_model():
    llm = OllamaLLM({"url": "http://x", "model": "qwen3:14b"}, transport=fake_ollama({"content": "ok"}, []))
    ok, msg = llm.probe()
    assert not ok and "ollama pull qwen3:14b" in msg
