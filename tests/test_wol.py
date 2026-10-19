"""Wake-on-LAN REST tests: named profiles, no real packets leave the box."""
import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import app

HEADERS = {"Authorization": "Bearer test-key-1"}


@pytest.fixture(autouse=True)
def _clean_profiles():
    main._wol_profiles.clear()
    yield
    main._wol_profiles.clear()


def _client():
    return TestClient(app)


def test_wol_status_empty_by_default():
    data = _client().get("/api/v1/wol/status", headers=HEADERS).json()
    assert data == {"configured": False, "mac": None, "profiles": []}


def test_wol_requires_bearer():
    assert _client().get("/api/v1/wol/status").status_code == 401


def test_wol_config_rejects_bad_mac():
    r = _client().post("/api/v1/wol/config", json={"mac": "nope"}, headers=HEADERS)
    assert r.status_code == 400


def test_wol_config_stores_multiple_named_profiles():
    c = _client()
    c.post(
        "/api/v1/wol/config",
        json={"name": "Home PC", "mac": "AA:BB:CC:DD:EE:01", "broadcastAddress": "192.168.1.255"},
        headers=HEADERS,
    )
    c.post(
        "/api/v1/wol/config",
        json={"name": "Office PC", "mac": "AA:BB:CC:DD:EE:02"},
        headers=HEADERS,
    )

    status = c.get("/api/v1/wol/status", headers=HEADERS).json()
    assert status["configured"] is True
    assert [p["name"] for p in status["profiles"]] == ["Home PC", "Office PC"]

    home, office = status["profiles"]
    assert home["mac"] == "AA:BB:CC:DD:EE:01"
    assert home["broadcastAddress"] == "192.168.1.255"
    assert office["mac"] == "AA:BB:CC:DD:EE:02"
    assert office["broadcastAddress"] == "255.255.255.255"


def test_wol_config_upserts_by_name():
    c = _client()
    c.post("/api/v1/wol/config", json={"name": "Home PC", "mac": "AA:BB:CC:DD:EE:01"}, headers=HEADERS)
    c.post("/api/v1/wol/config", json={"name": "Home PC", "mac": "AA:BB:CC:DD:EE:99"}, headers=HEADERS)

    profiles = c.get("/api/v1/wol/status", headers=HEADERS).json()["profiles"]
    assert len(profiles) == 1
    assert profiles[0]["mac"] == "AA:BB:CC:DD:EE:99"


def test_wol_config_defaults_name_when_absent():
    c = _client()
    c.post("/api/v1/wol/config", json={"mac": "AA:BB:CC:DD:EE:01"}, headers=HEADERS)
    profiles = c.get("/api/v1/wol/status", headers=HEADERS).json()["profiles"]
    assert profiles[0]["name"] == "Default"


def test_wol_wake_named_sends_only_that_packet(monkeypatch):
    sent = []

    def fake_send(mac, broadcast="255.255.255.255", port=9):
        sent.append((mac, broadcast, port))

    monkeypatch.setattr(main, "_send_magic_packet", fake_send)
    c = _client()
    c.post(
        "/api/v1/wol/config",
        json={"name": "Home PC", "mac": "AA:BB:CC:DD:EE:01", "broadcastAddress": "192.168.1.255"},
        headers=HEADERS,
    )
    c.post("/api/v1/wol/config", json={"name": "Office PC", "mac": "AA:BB:CC:DD:EE:02"}, headers=HEADERS)

    r = c.post("/api/v1/wol/wake", json={"name": "Office PC"}, headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["name"] == "Office PC"
    assert r.json()["mac"] == "AA:BB:CC:DD:EE:02"
    assert sent == [("AA:BB:CC:DD:EE:02", "255.255.255.255", 9)]

    c.post("/api/v1/wol/wake", json={"name": "Home PC"}, headers=HEADERS)
    assert sent[-1] == ("AA:BB:CC:DD:EE:01", "192.168.1.255", 9)


def test_wol_wake_without_name_uses_first_profile(monkeypatch):
    monkeypatch.setattr(main, "_send_magic_packet", lambda *a, **k: None)
    c = _client()
    c.post("/api/v1/wol/config", json={"name": "Home PC", "mac": "AA:BB:CC:DD:EE:01"}, headers=HEADERS)

    r = c.post("/api/v1/wol/wake", headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["name"] == "Home PC"


def test_wol_wake_unknown_profile_404(monkeypatch):
    monkeypatch.setattr(main, "_send_magic_packet", lambda *a, **k: None)
    c = _client()
    c.post("/api/v1/wol/config", json={"name": "Home PC", "mac": "AA:BB:CC:DD:EE:01"}, headers=HEADERS)

    r = c.post("/api/v1/wol/wake", json={"name": "Nope"}, headers=HEADERS)
    assert r.status_code == 404


def test_wol_wake_without_profiles_400():
    r = _client().post("/api/v1/wol/wake", headers=HEADERS)
    assert r.status_code == 400


def test_wol_delete_profile():
    c = _client()
    c.post("/api/v1/wol/config", json={"name": "Home PC", "mac": "AA:BB:CC:DD:EE:01"}, headers=HEADERS)

    r = c.delete("/api/v1/wol/config/Home%20PC", headers=HEADERS)
    assert r.status_code == 200
    assert c.get("/api/v1/wol/status", headers=HEADERS).json()["profiles"] == []


def test_wol_delete_unknown_profile_404():
    assert _client().delete("/api/v1/wol/config/Nope", headers=HEADERS).status_code == 404
