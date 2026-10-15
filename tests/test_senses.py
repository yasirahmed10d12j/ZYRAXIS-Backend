"""Senses tests: vision, weather, system, voice. Provider I/O mocked."""
import base64
import dataclasses

from fastapi.testclient import TestClient

import app.main as main

KEY = "test-key-1"
HEADERS = {"Authorization": "Bearer %s" % KEY}


def _client():
    return TestClient(main.app)


def test_wmo_mapping():
    from app.senses import describe_wmo
    assert describe_wmo(0) == "Clear sky"
    assert describe_wmo(95) == "Thunderstorm"
    assert describe_wmo(999).startswith("Unknown")


def test_weather_happy_and_validation(monkeypatch):
    async def fake_fetch(lat, lon):
        assert (lat, lon) == (28.61, 77.2)
        return {"temp_c": 30.0, "humidity": 50, "wind_kph": 10.0, "code": 1,
                "desc": "Mainly clear",
                "today": {"max_c": 33.0, "min_c": 25.0, "precip_prob": 5}}

    monkeypatch.setattr("app.main._fetch_weather", fake_fetch)
    c = _client()
    assert c.get("/api/v1/weather").status_code == 401
    r = c.get("/api/v1/weather", params={"lat": "28.61", "lon": "77.2"}, headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["desc"] == "Mainly clear"
    assert r.json()["today"]["max_c"] == 33.0
    assert c.get("/api/v1/weather", params={"lat": "abc", "lon": "1"}, headers=HEADERS).status_code == 400
    assert c.get("/api/v1/weather", params={"lat": "99", "lon": "1"}, headers=HEADERS).status_code == 400


def test_weather_provider_failure(monkeypatch):
    async def boom(lat, lon):
        raise RuntimeError("down")

    monkeypatch.setattr("app.main._fetch_weather", boom)
    r = _client().get("/api/v1/weather", params={"lat": "1", "lon": "1"}, headers=HEADERS)
    assert r.status_code == 502


def test_system_shape():
    r = _client().get("/api/v1/system", headers=HEADERS)
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data["cpu_pct"], (int, float))
    assert data["ram"]["total_gb"] > 0
    assert _client().get("/api/v1/system").status_code == 401


def test_vision_happy_and_missing_model(monkeypatch):
    async def fake_describe(image, question, model, host):
        assert question == "What?"
        return "a cat"

    monkeypatch.setattr("app.main._describe_image", fake_describe)
    c = _client()
    assert c.post("/api/v1/vision", json={}).status_code == 401
    r = c.post("/api/v1/vision", json={"image": "aGVsbG8=", "question": "What?"}, headers=HEADERS)
    assert r.status_code == 200
    assert r.json() == {"text": "a cat", "model": main.settings.vision_model}
    assert c.post("/api/v1/vision", json={}, headers=HEADERS).status_code == 400

    async def missing(image, question, model, host):
        raise LookupError("not pulled")

    monkeypatch.setattr("app.main._describe_image", missing)
    assert c.post("/api/v1/vision", json={"image": "eA=="}, headers=HEADERS).status_code == 503


def test_transcribe_silence_is_healthy():
    # 1s of digital silence through the REAL local model: must not crash,
    # must return a string (empty for silence).
    pcm = base64.b64encode(bytes(16000 * 2)).decode()
    r = _client().post("/api/v1/transcribe", json={"audio": pcm, "sample_rate": 16000},
                       headers=HEADERS)
    assert r.status_code == 200
    assert isinstance(r.json()["text"], str)


def test_transcribe_validation_and_missing_model(tmp_path, monkeypatch):
    c = _client()
    assert c.post("/api/v1/transcribe", json={}).status_code == 401
    body = {"audio": base64.b64encode(bytes(100)).decode()}
    assert c.post("/api/v1/transcribe", json={}, headers=HEADERS).status_code == 400
    assert c.post("/api/v1/transcribe", json={"audio": "!!!"}, headers=HEADERS).status_code == 400
    bad = dict(body, sample_rate=12345)
    assert c.post("/api/v1/transcribe", json=bad, headers=HEADERS).status_code == 400
    patched = dataclasses.replace(main.settings, vosk_model_dir=str(tmp_path / "nope"))
    monkeypatch.setattr("app.main.settings", patched)
    assert c.post("/api/v1/transcribe", json=body, headers=HEADERS).status_code == 503


def test_admin_weather_and_vision_validation():
    c = _client()
    assert c.post("/admin/weather", data={}).status_code == 401
    assert c.post("/admin/vision", data={}).status_code == 401
    r = c.post("/admin/weather?key=%s" % KEY, data={"lat": "xx", "lon": "yy", "key": KEY})
    assert r.status_code in (200, 303)
    r = c.post("/admin/vision?key=%s" % KEY, data={"image": "", "key": KEY})
    assert r.status_code in (200, 303)
    assert main.stats.last_vision == "No image received."


def test_config_sense_defaults():
    assert main.settings.vision_model == "moondream"
    assert main.settings.ollama_host == "http://localhost:11434"
    assert main.settings.vosk_model_dir.endswith("vosk-model-small-en-us-0.15")
