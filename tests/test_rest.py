"""REST tests with the provider layer mocked — zero API credits used."""
from fastapi.testclient import TestClient

from app import providers as llm
from app.main import app

HEADERS = {"Authorization": "Bearer test-key-1"}
BODY = {
    "requestId": "11111111-1111-1111-1111-111111111111",
    "text": "what time is it",
    "context": {"systemPrompt": None, "messages": [], "memories": []},
}


def _client():
    return TestClient(app)


def test_healthz():
    assert _client().get("/healthz").json() == {"ok": True}


def test_root_info():
    data = _client().get("/").json()
    assert data["service"] == "zyraxis-backend"
    assert data["protocol"] == 1


def test_chat_requires_bearer():
    r = _client().post("/api/v1/chat", json=BODY)
    assert r.status_code == 401


def test_chat_rejects_bad_key():
    r = _client().post(
        "/api/v1/chat", json=BODY, headers={"Authorization": "Bearer wrong"}
    )
    assert r.status_code == 401


def test_chat_rejects_empty_text():
    r = _client().post("/api/v1/chat", json={"text": "  "}, headers=HEADERS)
    assert r.status_code == 400


def test_chat_happy_path_mocked(monkeypatch):
    async def fake_text(*, context, text, settings):
        assert text == "what time is it"
        return "mocked answer"

    monkeypatch.setattr("app.main.llm.generate_text", fake_text)
    r = _client().post("/api/v1/chat", json=BODY, headers=HEADERS)
    assert r.status_code == 200
    data = r.json()
    assert data["text"] == "mocked answer"
    assert data["toolCalls"] == []
    assert data["requestId"] == BODY["requestId"]


def test_chat_rate_limited_maps_429(monkeypatch):
    async def fake_text(*, context, text, settings):
        raise llm.RateLimitedError()

    monkeypatch.setattr("app.main.llm.generate_text", fake_text)
    r = _client().post("/api/v1/chat", json=BODY, headers=HEADERS)
    assert r.status_code == 429


def test_chat_provider_failure_maps_502(monkeypatch):
    async def fake_text(*, context, text, settings):
        raise llm.ProviderError("boom")

    monkeypatch.setattr("app.main.llm.generate_text", fake_text)
    r = _client().post("/api/v1/chat", json=BODY, headers=HEADERS)
    assert r.status_code == 502


def test_chat_accepts_other_api_version_mocked(monkeypatch):
    async def fake_text(*, context, text, settings):
        return "ok"

    monkeypatch.setattr("app.main.llm.generate_text", fake_text)
    r = _client().post("/api/v9/chat", json=BODY, headers=HEADERS)
    assert r.status_code == 200


def test_chat_bad_auth_on_primary_falls_through_to_groq(monkeypatch):
    async def fake_gemini(*args, **kwargs):
        raise llm.ProviderAuthError("bad gemini key")

    async def fake_groq(*args, **kwargs):
        return "via groq"

    monkeypatch.setattr("app.providers._gemini_text", fake_gemini)
    monkeypatch.setattr("app.providers._groq_text_once", fake_groq)
    r = _client().post("/api/v1/chat", json=BODY, headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["text"] == "via groq"


def test_chat_rejects_oversize_body(monkeypatch):
    monkeypatch.setattr("app.main.MAX_BODY_BYTES", 10)
    r = _client().post("/api/v1/chat", json=BODY, headers=HEADERS)
    assert r.status_code == 413
