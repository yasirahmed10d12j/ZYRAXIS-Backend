"""Local-provider unit tests: Ollama wiring + fallback. No network."""
import asyncio

from app import providers as llm
from app.config import Settings

LOCAL_SETTINGS = Settings(
    backend_keys=frozenset({"k"}),
    provider_order=("local", "gemini", "groq"),
    gemini_api_key="fake",
    groq_api_key="fake",
    local_model="qwen2.5:3b",
    local_base="http://localhost:11434/v1/chat/completions",
)


def test_local_enabled_without_key():
    assert llm._enabled(LOCAL_SETTINGS) == ["local", "gemini", "groq"]


def test_local_not_enabled_when_unordered():
    s = Settings(backend_keys=frozenset({"k"}), gemini_api_key="fake")
    assert "local" not in llm._enabled(s)


def test_local_request_shape():
    body = llm._local_request([{"role": "user", "content": "hi"}], LOCAL_SETTINGS, stream=True)
    assert body["model"] == "qwen2.5:3b"
    assert body["stream"] is True
    assert body["messages"][-1] == {"role": "user", "content": "hi"}


def test_local_failure_falls_through_to_gemini(monkeypatch):
    async def fake_local(*args, **kwargs):
        raise llm.ProviderError("local down", retryable=True)

    async def fake_gemini(*args, **kwargs):
        return "via gemini"

    monkeypatch.setattr("app.providers._local_text_once", fake_local)
    monkeypatch.setattr("app.providers._gemini_text", fake_gemini)
    out = asyncio.run(llm.generate_text(context={}, text="hi", settings=LOCAL_SETTINGS))
    assert out == "via gemini"
