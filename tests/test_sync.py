"""Ecosystem sync tests: devices, shared memory/tasks API, persistence, push."""
import json

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.syncstore import MemoryStore, TaskStore

KEY = "test-key-1"
HEADERS = {"Authorization": "Bearer %s" % KEY}
RID = "88888888-8888-8888-8888-888888888888"


@pytest.fixture
def isolated_stores(tmp_path, monkeypatch):
    mem = MemoryStore(str(tmp_path / "memory.json"))
    tasks = TaskStore(str(tmp_path / "tasks.json"))
    monkeypatch.setattr("app.main.memory_store", mem)
    monkeypatch.setattr("app.main.task_store", tasks)
    return mem, tasks


def _client():
    return TestClient(main.app)


def _frame(mtype, rid=RID, payload=None, version=1):
    return json.dumps(
        {"version": version, "type": mtype, "requestId": rid, "payload": payload or {}}
    )


def test_sync_requires_key(isolated_stores):
    c = _client()
    assert c.get("/api/v1/devices").status_code == 401
    assert c.get("/api/v1/memory").status_code == 401
    assert c.post("/api/v1/memory", json={"content": "x"}).status_code == 401
    assert c.get("/api/v1/tasks").status_code == 401
    assert c.post("/api/v1/tasks", json={"title": "x"}).status_code == 401


def test_memory_crud(isolated_stores):
    c = _client()
    r = c.post("/api/v1/memory", json={"content": "Rehan likes cricket",
                                       "tags": ["contact"], "importance": 0.9},
               headers=HEADERS)
    assert r.status_code == 200
    mid = r.json()["id"]
    assert c.post("/api/v1/memory", json={"content": "  "}, headers=HEADERS).status_code == 400
    found = c.get("/api/v1/memory", params={"query": "cricket"}, headers=HEADERS).json()["memories"]
    assert [m["id"] for m in found] == [mid]
    assert c.delete("/api/v1/memory/%s" % mid, headers=HEADERS).status_code == 200
    assert c.delete("/api/v1/memory/%s" % mid, headers=HEADERS).status_code == 404


def test_tasks_crud(isolated_stores):
    c = _client()
    r = c.post("/api/v1/tasks", json={"title": "Pay bill", "priority": "high"}, headers=HEADERS)
    assert r.status_code == 200
    tid = r.json()["id"]
    assert c.post("/api/v1/tasks", json={"title": "  "}, headers=HEADERS).status_code == 400
    active = c.get("/api/v1/tasks", params={"filter": "active"}, headers=HEADERS).json()["tasks"]
    assert [t["id"] for t in active] == [tid]
    assert c.post("/api/v1/tasks/%s/complete" % tid, headers=HEADERS).status_code == 200
    assert c.get("/api/v1/tasks", params={"filter": "active"}, headers=HEADERS).json()["tasks"] == []
    done = c.get("/api/v1/tasks", params={"filter": "completed"}, headers=HEADERS).json()["tasks"]
    assert [t["id"] for t in done] == [tid]
    assert c.post("/api/v1/tasks/nope/complete", headers=HEADERS).status_code == 404
    r2 = c.post("/api/v1/tasks", json={"title": "Later"}, headers=HEADERS).json()
    assert c.post("/api/v1/tasks/%s/snooze" % r2["id"], json={"until": "2030-01-01T09:00:00"},
                  headers=HEADERS).status_code == 200
    assert c.post("/api/v1/tasks/%s/cancel" % r2["id"], headers=HEADERS).status_code == 200


def test_stores_persist(tmp_path):
    mp, tp = str(tmp_path / "m.json"), str(tmp_path / "t.json")
    m1, t1 = MemoryStore(mp), TaskStore(tp)
    mid = m1.save("hello", ["greet"], 0.8)["id"]
    tid = t1.create("job")["id"]
    m2, t2 = MemoryStore(mp), TaskStore(tp)
    assert [m["id"] for m in m2.search("hello")] == [mid]
    assert [t["id"] for t in t2.list("active")] == [tid]


def test_device_claim_and_list(isolated_stores):
    c = _client()
    with c.websocket_connect("/live") as ws:
        ws.send_text(_frame("session_start", payload={
            "sessionId": RID, "deviceId": "dev-1", "deviceName": "Yasir's iPhone",
            "clientModel": "iPhone", "appVersion": "0.5.0"}))
        ws.send_text(_frame("ping", payload={"timestamp": 1.0}))
        assert json.loads(ws.receive_text())["type"] == "pong"
        devs = c.get("/api/v1/devices", headers=HEADERS).json()["devices"]
    assert any(d["id"] == "dev-1" and d["name"] == "Yasir's iPhone" and d["online"] for d in devs)


def test_device_fallback_without_id(isolated_stores):
    c = _client()
    with c.websocket_connect("/live") as ws:
        ws.send_text(_frame("session_start", payload={"sessionId": RID}))
        ws.send_text(_frame("ping", payload={"timestamp": 1.0}))
        assert json.loads(ws.receive_text())["type"] == "pong"
        devs = c.get("/api/v1/devices", headers=HEADERS).json()["devices"]
    assert any(d["id"] == RID for d in devs)


def test_remote_cmd_reaches_device_by_id(isolated_stores):
    c = _client()
    with c.websocket_connect("/live") as ws:
        ws.send_text(_frame("session_start", payload={
            "sessionId": RID, "deviceId": "dev-9", "deviceName": "Test"}))
        r = c.post("/admin/cmd?key=%s" % KEY,
                   data={"device": "dev-9", "command": "open youtube", "key": KEY})
        assert r.status_code in (200, 303)
        got = json.loads(ws.receive_text())
    assert got["type"] == "remote_command"
    assert got["payload"] == {"command": "open youtube"}
