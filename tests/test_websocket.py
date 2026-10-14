"""WebSocket tests with streamed generation mocked — zero API credits used.

Mirrors the iOS adapter rules: version==1, UUID requestId echo, 1-based
strictly-increasing delta seq, complete with full text.
"""
import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import providers as llm
from app.main import app

RID = "22222222-2222-2222-2222-222222222222"


def _frame(mtype, rid=RID, payload=None, version=1):
    return json.dumps(
        {"version": version, "type": mtype, "requestId": rid, "payload": payload or {}}
    )


def _client():
    return TestClient(app)


def _mock_stream(monkeypatch, pieces):
    async def fake_stream(*, context, text, settings):
        for p in pieces:
            yield p

    monkeypatch.setattr("app.main.llm.generate_stream", fake_stream)


def test_streaming_happy_path(monkeypatch):
    _mock_stream(monkeypatch, ["Hel", "lo"])
    with _client().websocket_connect("/live") as ws:
        ws.send_text(_frame("session_start", payload={"sessionId": RID}))
        ws.send_text(_frame("user_message", payload={"text": "hi", "context": {}}))
        got = [json.loads(ws.receive_text()) for _ in range(3)]
    assert [g["type"] for g in got] == [
        "assistant_delta",
        "assistant_delta",
        "assistant_complete",
    ]
    assert [g["version"] for g in got] == [1, 1, 1]
    assert [g["requestId"] for g in got] == [RID, RID, RID]
    assert [g["payload"]["seq"] for g in got[:2]] == [1, 2]
    assert [g["payload"]["delta"] for g in got[:2]] == ["Hel", "lo"]
    assert got[2]["payload"]["fullText"] == "Hello"
    assert got[2]["payload"]["toolCalls"] == []


def test_ping_gets_pong():
    with _client().websocket_connect("/live") as ws:
        ws.send_text(_frame("ping", payload={"timestamp": 99.0}))
        reply = json.loads(ws.receive_text())
    assert reply["type"] == "pong"
    assert reply["requestId"] == RID
    assert reply["payload"] == {"timestamp": 99.0}


def test_unknown_type_ignored_then_pong():
    # Unknown frames must produce nothing; the next ping still gets its pong,
    # proving the socket stayed healthy and the frame was skipped, not queued.
    with _client().websocket_connect("/live") as ws:
        ws.send_text(_frame("future_magic", payload={"x": 1}))
        ws.send_text(_frame("ping", payload={"timestamp": 1.0}))
        reply = json.loads(ws.receive_text())
    assert reply["type"] == "pong"


def test_version_mismatch_gets_error():
    with _client().websocket_connect("/live") as ws:
        ws.send_text(_frame("user_message", payload={"text": "hi"}, version=999))
        reply = json.loads(ws.receive_text())
    assert reply["type"] == "assistant_error"
    assert reply["payload"]["code"] == "VERSION_MISMATCH"
    assert reply["payload"]["retryable"] is False


def test_tool_result_tolerated():
    with _client().websocket_connect("/live") as ws:
        ws.send_text(
            _frame("tool_result", payload={"toolName": "t", "outputJSON": "{}"})
        )
        ws.send_text(_frame("ping", payload={"timestamp": 2.0}))
        reply = json.loads(ws.receive_text())
    assert reply["type"] == "pong"


def test_missing_request_id_ignored():
    with _client().websocket_connect("/live") as ws:
        ws.send_text(json.dumps({"version": 1, "type": "ping", "payload": {}}))
        ws.send_text(_frame("ping", payload={"timestamp": 3.0}))
        reply = json.loads(ws.receive_text())
    assert reply["type"] == "pong"


def test_provider_rate_limit_maps_retryable_error(monkeypatch):
    async def fake_stream(*, context, text, settings):
        raise llm.RateLimitedError()
        yield None  # pragma: no cover — makes this an async generator

    monkeypatch.setattr("app.main.llm.generate_stream", fake_stream)
    with _client().websocket_connect("/live") as ws:
        ws.send_text(_frame("user_message", payload={"text": "hi"}))
        reply = json.loads(ws.receive_text())
    assert reply["type"] == "assistant_error"
    assert reply["payload"]["code"] == "RATE_LIMITED"
    assert reply["payload"]["retryable"] is True


def test_ws_wrong_key_rejected():
    # Server closes with 1008; the TestClient surfaces the close on
    # receive (older versions raised on connect) — either way the key is
    # rejected and no reply ever arrives.
    with _client().websocket_connect("/live?key=wrong") as ws:
        ws.send_text(_frame("ping", payload={"timestamp": 1.0}))
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()
