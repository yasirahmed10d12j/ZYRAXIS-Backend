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
    # Connect card shows copy-ready onboarding values to the key-holding
    # admin (no new exposure: the page itself is 401 without the key).
    assert "Connect a new iPhone" in r.text
    assert "ws://" in r.text and "/live" in r.text
    assert KEY in r.text


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


def test_connect_info_shape():
    info = main._connect_info()
    assert info["ws"].endswith("/live") or info["ws"] == "unavailable"
    assert info["base"].startswith("http://") or info["base"] == "unavailable"
    assert info["key"]  # fail-closed notice or a real key, never empty


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


@pytest.fixture
def isolated_hub(tmp_path, monkeypatch):
    from app.syncstore import MemoryStore, TaskStore
    monkeypatch.setattr("app.main.memory_store", MemoryStore(str(tmp_path / "m.json")))
    monkeypatch.setattr("app.main.task_store", TaskStore(str(tmp_path / "t.json")))


def test_export_downloads_transcript(monkeypatch, isolated_hub):
    async def fake_text(*, context, text, settings):
        return "export me"

    monkeypatch.setattr("app.main.llm.generate_text", fake_text)
    _client().post("/api/v1/chat", json={"text": "hello export"},
                   headers={"Authorization": "Bearer %s" % KEY})
    r = _client().get("/admin/export?key=%s" % KEY)
    assert r.status_code == 200
    assert "hello export" in r.text and "export me" in r.text
    assert _client().get("/admin/export").status_code == 401


def test_broadcast_reaches_live_device():
    rid = "99999999-9999-9999-9999-999999999999"
    frame = json.dumps({"version": 1, "type": "session_start", "requestId": rid,
                        "payload": {"sessionId": "sess-bc-1"}})
    with _client().websocket_connect("/live") as ws:
        ws.send_text(frame)
        r = _client().post("/admin/broadcast?key=%s" % KEY,
                           data={"command": "open youtube", "key": KEY})
        assert r.status_code in (200, 303)
        got = json.loads(ws.receive_text())
    assert got["type"] == "remote_command"
    assert got["payload"] == {"command": "open youtube"}


def test_note_and_task_from_dashboard(isolated_hub):
    c = _client()
    assert c.post("/admin/note?key=%s" % KEY, data={"content": "dash note", "key": KEY}).status_code in (200, 303)
    assert c.post("/admin/task?key=%s" % KEY, data={"title": "dash task", "key": KEY}).status_code in (200, 303)
    mems = c.get("/api/v1/memory", params={"query": "dash note"},
                 headers={"Authorization": "Bearer %s" % KEY}).json()["memories"]
    assert len(mems) == 1
    tasks = c.get("/api/v1/tasks", params={"filter": "active"},
                  headers={"Authorization": "Bearer %s" % KEY}).json()["tasks"]
    assert any(t["title"] == "dash task" for t in tasks)
    assert c.post("/admin/note", data={"content": "x"}).status_code == 401
    assert c.post("/admin/task", data={"title": "x"}).status_code == 401
    assert c.post("/admin/broadcast", data={"command": "x"}).status_code == 401


def test_model_unload_calls_ollama(monkeypatch):
    calls = []

    class FakeResp:
        status_code = 200

        def json(self):
            return {"models": []}

    class FakeCM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kwargs):
            calls.append(url)
            return FakeResp()

        async def get(self, url, **kwargs):
            calls.append(url)
            return FakeResp()

    class FakeHttpx:
        def AsyncClient(self, *a, **k):
            return FakeCM()

        def Timeout(self, *a, **k):
            return None

    monkeypatch.setattr("app.main.httpx", FakeHttpx())
    r = _client().post("/admin/model-unload?key=%s" % KEY, data={"key": KEY})
    assert r.status_code in (200, 303)
    assert any("/api/generate" in u for u in calls)
    assert _client().post("/admin/model-unload", data={}).status_code == 401
