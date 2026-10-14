"""Provider-layer unit tests: prompt shaping + fallback policy. No network."""
import asyncio

import pytest

from app import providers as llm
from app.config import Settings

SETTINGS = Settings(
    backend_keys=frozenset({"k"}),
    gemini_api_key="fake",
    groq_api_key="fake",
)


def test_gemini_contents_alternates_from_user():
    history = [
        {"role": "user", "text": "u1"},
        {"role": "user", "text": "u2"},
        {"role": "assistant", "text": "a1"},
        {"role": "assistant", "text": "a2"},
    ]
    msgs = llm.build_messages({"messages": history}, "u3", SETTINGS)
    contents = llm._gemini_contents(msgs)
    roles = [c["role"] for c in contents]
    assert roles[0] == "user"
    assert all(a != b for a, b in zip(roles, roles[1:])), roles
    joined = " ".join(c["parts"][0]["text"] for c in contents)
    for snippet in ("u1", "u2", "a1", "a2", "u3"):
        assert snippet in joined


def test_gemini_contents_drops_leading_model_turn():
    # Unreachable in practice (turns always end with the current user text),
    # but the API rejects a leading model turn, so drop it deterministically.
    msgs = llm.build_messages({"messages": [{"role": "assistant", "text": "stray"}]}, "u1", SETTINGS)
    contents = llm._gemini_contents(msgs)
    assert [c["role"] for c in contents] == ["user"]
    assert "u1" in contents[0]["parts"][0]["text"]


def test_gemini_contents_simple_turn():
    msgs = llm.build_messages({}, "hello", SETTINGS)
    contents = llm._gemini_contents(msgs)
    assert [c["role"] for c in contents] == ["user"]
    assert contents[0]["parts"][0]["text"] == "hello"


async def _collect(agen):
    out = []
    async for chunk in agen:
        out.append(chunk)
    return out


def test_no_provider_configured():
    empty = Settings(backend_keys=frozenset({"k"}))
    with pytest.raises(llm.ProviderConfigError):
        asyncio.run(llm.generate_text(context={}, text="hi", settings=empty))
    with pytest.raises(llm.ProviderConfigError):
        asyncio.run(_collect(llm.generate_stream(context={}, text="hi", settings=empty)))
