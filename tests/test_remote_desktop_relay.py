"""Remote Desktop relay tests.

The hub only routes frames: a phone and a laptop that announced the same
session key exchange screen frames (laptop → phone) and input events
(phone → laptop). Nothing is delivered across different keys.
"""
import json
import threading

import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import app


def _frame(mtype, rid="rd-1", payload=None):
    return json.dumps(
        {"version": 1, "type": mtype, "requestId": rid, "payload": payload or {}}
    )


def _announce(key):
    return _frame(
        "remote-desktop-announce",
        "rd-announce",
        {"deviceId": "dev-1", "sessionKey": key},
    )


def _client():
    return TestClient(app)


def _receive_or_none(ws, timeout=0.5):
    """Receive a frame, or None when nothing arrives within `timeout`."""
    box = {"raw": None}

    def run():
        try:
            box["raw"] = ws.receive_text()
        except Exception:
            box["raw"] = None

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout)
    return box["raw"]


@pytest.fixture(autouse=True)
def _clean_relay():
    """Isolate relay state + rate-limit buckets between tests (all TestClient
    sockets share one client IP, so buckets would leak across tests)."""
    main._rd_peers.clear()
    main.limiter._msg.clear()
    main.limiter._relay.clear()
    yield
    main._rd_peers.clear()
    main.limiter._msg.clear()
    main.limiter._relay.clear()


def test_screen_frame_reaches_phone_with_same_key():
    with _client().websocket_connect("/live") as phone, _client().websocket_connect(
        "/live"
    ) as laptop:
        phone.send_text(_announce("KEY-1"))
        laptop.send_text(_announce("KEY-1"))
        laptop.send_text(
            _frame(
                "remote-desktop-screen",
                "rd-screen",
                {"sessionKey": "KEY-1", "frameB64": "AAAA", "width": 8, "height": 6},
            )
        )
        got = json.loads(phone.receive_text())
    assert got["type"] == "remote-desktop-screen"
    assert got["payload"]["frameB64"] == "AAAA"
    assert got["payload"]["width"] == 8


def test_input_reaches_laptop_with_same_key():
    with _client().websocket_connect("/live") as phone, _client().websocket_connect(
        "/live"
    ) as laptop:
        phone.send_text(_announce("KEY-2"))
        laptop.send_text(_announce("KEY-2"))
        phone.send_text(
            _frame(
                "remote-desktop-input",
                "rd-in",
                {"sessionKey": "KEY-2", "kind": "mouse", "x": 10, "y": 20, "button": "left"},
            )
        )
        got = json.loads(laptop.receive_text())
    assert got["type"] == "remote-desktop-input"
    assert got["payload"]["kind"] == "mouse"
    assert got["payload"]["x"] == 10


def test_frames_do_not_cross_session_keys():
    with _client().websocket_connect("/live") as phone_a, _client().websocket_connect(
        "/live"
    ) as phone_b, _client().websocket_connect("/live") as laptop:
        phone_a.send_text(_announce("KEY-A"))
        phone_b.send_text(_announce("KEY-B"))
        laptop.send_text(_announce("KEY-B"))
        laptop.send_text(
            _frame(
                "remote-desktop-screen",
                "rd-screen",
                {"sessionKey": "KEY-B", "frameB64": "BBBB", "width": 4, "height": 3},
            )
        )
        assert _receive_or_none(phone_b) is not None
        assert _receive_or_none(phone_a) is None


def test_unkeyed_announce_routes_nothing():
    with _client().websocket_connect("/live") as laptop:
        laptop.send_text(
            _frame(
                "remote-desktop-announce",
                "rd-announce",
                {"deviceId": "dev-1", "sessionKey": None},
            )
        )
        laptop.send_text(
            _frame(
                "remote-desktop-screen",
                "rd-screen",
                {"sessionKey": "NONE", "frameB64": "CCCC", "width": 1, "height": 1},
            )
        )
        assert _receive_or_none(laptop) is None


def test_relay_registry_cleared_on_disconnect():
    with _client().websocket_connect("/live") as phone:
        phone.send_text(_announce("KEY-X"))
        assert "KEY-X" in main._rd_peers.values()
    assert "KEY-X" not in main._rd_peers.values()


def test_screen_stream_survives_the_chat_cap():
    """Video must not be throttled by the 120/min chat-message cap."""
    with _client().websocket_connect("/live") as phone, _client().websocket_connect(
        "/live"
    ) as laptop:
        phone.send_text(_announce("KEY-V"))
        laptop.send_text(_announce("KEY-V"))
        for i in range(130):
            laptop.send_text(
                _frame(
                    "remote-desktop-screen",
                    "rd-screen",
                    {"sessionKey": "KEY-V", "frameB64": "F", "width": 1, "height": 1, "at": i},
                )
            )
        got = 0
        while _receive_or_none(phone, timeout=2.0) is not None:
            got += 1
            if got >= 130:
                break
    assert got == 130


def test_chat_cap_still_applies_to_normal_frames():
    """The relay quota must not switch off the chat flood guard."""
    with _client().websocket_connect("/live") as ws:
        for i in range(125):
            ws.send_text(
                json.dumps({"version": 99, "type": "nope", "requestId": f"r{i}", "payload": {}})
            )
        replies = 0
        while _receive_or_none(ws, timeout=2.0) is not None:
            replies += 1
            if replies > 130:
                break
    assert replies == 120
