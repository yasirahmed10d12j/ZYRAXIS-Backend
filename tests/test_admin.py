"""Admin dashboard tests: auth gate, device tracking, runtime config. No network."""
import json

import pytest
from fastapi.testclient import TestClient

import app.main as main

KEY = "test-key-1"


@pytest.fixture
def restore_settings():
    saved = main.settings
    yield
    main.settings = saved


def _client():
    return TestClient(app_main_app())


def app_main_app():
    return main.app


def test_admin_requires_key():
    assert _client().get("/admin").status_code == 401
    assert _client().get("/admin?key=wrong").status_code == 401


def test_admin_page_with_key():
    r = _client().get("/admin?key=%s" % KEY)
    assert r.status_code == 200
    assert "ZYRAXIS Server" in r.text
    assert KEY not in r.text.split("hidden")[0]  # key not leaked in visible markup


def test_admin_config_rejected_without_key(restore_settings):
    r = _client().post("/admin/config", data={"provider_order": "local"})
    assert r.status_code == 401


def test_admin_config_updates_runtime(restore_settings):
    c = _client()
    r = c.post("/admin/config?key=%s" % KEY, data={
        "provider_order": "local,groq",
        "max_output_tokens": "512",
        "temperature": "0.5",
        "key": KEY,
    })
    assert r.status_code in (200, 303)
    assert main.settings.provider_order == ("local", "groq")
    assert main.settings.max_output_tokens == 512
    assert main.settings.temperature == 0.5


def test_session_start_records_device():
    rid = "44444444-4444-4444-4444-444444444444"
    frame = json.dumps({"version": 1, "type": "session_start", "requestId": rid,
                        "payload": {"sessionId": "sess-admin-1", "clientModel": "iPhone12,5"}})
    ping = json.dumps({"version": 1, "type": "ping", "requestId": rid,
                       "payload": {"timestamp": 1.0}})
    with _client().websocket_connect("/live") as ws:
        ws.send_text(frame)
        ws.send_text(ping)
        reply = json.loads(ws.receive_text())
    assert reply["type"] == "pong"
    dev = main.stats.devices.get("sess-admin-1")
    assert dev is not None
    assert dev["model"] == "iPhone12,5"


def test_rest_turn_mirrored_to_transcript(monkeypatch):
    async def fake_text(*, context, text, settings):
        return "mirrored reply"

    monkeypatch.setattr("app.main.llm.generate_text", fake_text)
    before = len(main.stats.transcript)
    r = _client().post("/api/v1/chat", json={"text": "mirror me", "requestId": "55555555-5555-5555-5555-555555555555"},
                       headers={"Authorization": "Bearer %s" % KEY})
    assert r.status_code == 200
    assert len(main.stats.transcript) == before + 1
    assert main.stats.transcript[0]["q"] == "mirror me"
    assert main.stats.transcript[0]["a"] == "mirrored reply"


def test_remote_command_frame_shape():
    from app.protocol import remote_command_frame
    f = remote_command_frame("66666666-6666-6666-6666-666666666666", "open youtube")
    assert f["version"] == 1
    assert f["type"] == "remote_command"
    assert f["payload"] == {"command": "open youtube"}


def test_cmd_queues_for_offline_device():
    main.stats.pending.pop("ghost-dev", None)
    r = _client().post("/admin/cmd?key=%s" % KEY,
                       data={"device": "ghost-dev", "command": "open youtube", "key": KEY})
    assert r.status_code in (200, 303)
    assert main.stats.pending.get("ghost-dev") == ["open youtube"]
    main.stats.pending.pop("ghost-dev", None)


def test_cmd_rejected_without_key():
    assert _client().post("/admin/cmd", data={"device": "x", "command": "y"}).status_code == 401
    assert _client().post("/admin/ping", data={"device": "x"}).status_code == 401
    assert _client().post("/admin/clear").status_code == 401


def test_ping_offline_device_redirects():
    r = _client().post("/admin/ping?key=%s" % KEY, data={"device": "ghost-dev", "key": KEY})
    assert r.status_code in (200, 303)


def test_clear_empties_transcript(monkeypatch):
    async def fake_text(*, context, text, settings):
        return "x"

    monkeypatch.setattr("app.main.llm.generate_text", fake_text)
    _client().post("/api/v1/chat", json={"text": "to clear"},
                   headers={"Authorization": "Bearer %s" % KEY})
    assert len(main.stats.transcript) >= 1
    r = _client().post("/admin/clear?key=%s" % KEY, data={"key": KEY})
    assert r.status_code in (200, 303)
    assert len(main.stats.transcript) == 0
