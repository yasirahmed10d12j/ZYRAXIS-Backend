"""Provider layer: local Ollama (opt-in) + Gemini (primary) + Groq (fallback) over plain HTTPS/localhost.

No vendor SDKs — direct REST/SSE via httpx keeps the image tiny and the
behavior explicit. Streaming yields text chunks; callers assign sequence
numbers. Raises ProviderError subclasses; never returns partial lies.
"""
from __future__ import annotations

import json
import logging

import httpx

log = logging.getLogger("zyraxis.providers")

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
GROQ_BASE = "https://api.groq.com/openai/v1/chat/completions"


class ProviderError(Exception):
    def __init__(self, message: str = "provider failure", retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class RateLimitedError(ProviderError):
    def __init__(self, message: str = "provider rate limited") -> None:
        super().__init__(message, retryable=True)


class ProviderAuthError(ProviderError):
    def __init__(self, message: str = "provider credentials rejected") -> None:
        super().__init__(message, retryable=False)


class ProviderConfigError(ProviderError):
    def __init__(self, message: str = "no provider configured") -> None:
        super().__init__(message, retryable=False)


def _raise_for_status(status: int, body: str, provider: str) -> None:
    if status == 429:
        raise RateLimitedError("%s rate limited (HTTP 429)" % provider)
    if status in (401, 403):
        raise ProviderAuthError("%s rejected credentials (HTTP %d)" % (provider, status))
    if status == 404:
        raise ProviderError(
            "%s: model not found (HTTP 404 — check model ID)" % provider, retryable=False
        )
    if status >= 500:
        raise ProviderError("%s server error (HTTP %d)" % (provider, status), retryable=True)
    raise ProviderError("%s error (HTTP %d): %s" % (provider, status, body[:200]), retryable=False)


def build_messages(context: dict, text: str, settings) -> list:
    """Fold iOS ConversationContext into OpenAI-style messages + system text."""
    context = context or {}
    system_parts = [settings.system_prompt]
    if context.get("systemPrompt"):
        system_parts.append(str(context["systemPrompt"]))
    memories = context.get("memories") or []
    if memories:
        lines = []
        for m in memories[:8]:
            if isinstance(m, dict) and m.get("content"):
                lines.append("- %s" % str(m["content"])[:500])
        if lines:
            system_parts.append("User memories:\n" + "\n".join(lines))
    info_bits = []
    if context.get("assistantState"):
        info_bits.append("assistantState=%s" % context["assistantState"])
    if context.get("availableTools"):
        info_bits.append("tools=%s" % ",".join(map(str, context["availableTools"][:16])))
    device = context.get("device") or {}
    if isinstance(device, dict):
        if device.get("deviceModel"):
            info_bits.append("device=%s" % device["deviceModel"])
        if device.get("osVersion"):
            info_bits.append("os=%s" % device["osVersion"])
    if info_bits:
        system_parts.append("Session info: " + "; ".join(info_bits))
    messages = []
    for m in (context.get("messages") or [])[-20:]:
        if not isinstance(m, dict):
            continue
        role = "assistant" if str(m.get("role", "")).lower() == "assistant" else "user"
        content = str(m.get("text", ""))[:4000]
        if content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": text})
    return [{"role": "system", "content": "\n\n".join(system_parts)}] + messages


def _gemini_contents(messages: list) -> list:
    # Gemini requires user/model alternation starting with user: merge
    # consecutive same-role turns and drop any leading model turns (the
    # trailing current-user message guarantees a non-empty result).
    merged: list = []
    for m in messages:
        if m["role"] == "system":
            continue
        role = "model" if m["role"] == "assistant" else "user"
        if merged and merged[-1]["role"] == role:
            merged[-1]["parts"][0]["text"] += "\n\n" + m["content"]
        else:
            merged.append({"role": role, "parts": [{"text": m["content"]}]})
    while merged and merged[0]["role"] != "user":
        merged.pop(0)
    return merged


def _gemini_system(messages: list) -> dict:
    sys_texts = [m["content"] for m in messages if m["role"] == "system"]
    return {"parts": [{"text": "\n\n".join(sys_texts)}]} if sys_texts else {}


async def _gemini_text(prompt_messages: list, settings) -> str:
    url = "%s/%s:generateContent" % (GEMINI_BASE, settings.gemini_model)
    body = {
        "systemInstruction": _gemini_system(prompt_messages),
        "contents": _gemini_contents(prompt_messages),
        "generationConfig": {
            "maxOutputTokens": settings.max_output_tokens,
            "temperature": settings.temperature,
        },
    }
    params = {"key": settings.gemini_api_key}
    async with httpx.AsyncClient(timeout=httpx.Timeout(25.0)) as client:
        resp = await client.post(url, params=params, json=body)
    if resp.status_code != 200:
        _raise_for_status(resp.status_code, resp.text, "gemini")
    try:
        data = resp.json()
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise ProviderError("gemini: undecodable response (%s)" % e, retryable=True)


async def _gemini_stream(prompt_messages: list, settings):
    url = "%s/%s:streamGenerateContent" % (GEMINI_BASE, settings.gemini_model)
    body = {
        "systemInstruction": _gemini_system(prompt_messages),
        "contents": _gemini_contents(prompt_messages),
        "generationConfig": {
            "maxOutputTokens": settings.max_output_tokens,
            "temperature": settings.temperature,
        },
    }
    params = {"key": settings.gemini_api_key, "alt": "sse"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=120.0)) as client:
        async with client.stream("POST", url, params=params, json=body) as resp:
            if resp.status_code != 200:
                text = await resp.aread()
                _raise_for_status(resp.status_code, text.decode("utf-8", "replace"), "gemini")
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload:
                    continue
                try:
                    data = json.loads(payload)
                except ValueError:
                    continue
                try:
                    parts = data["candidates"][0]["content"]["parts"]
                    chunk = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
                except (KeyError, IndexError, TypeError):
                    continue
                if chunk:
                    yield chunk


def _groq_request(prompt_messages: list, settings, stream: bool = False):
    headers = {"Authorization": "Bearer %s" % settings.groq_api_key}
    body = {
        "model": settings.groq_model,
        "messages": prompt_messages,
        "temperature": settings.temperature,
        "max_tokens": settings.max_output_tokens,
        "stream": stream,
    }
    return headers, body


async def _groq_text_once(prompt_messages: list, settings) -> str:
    headers, body = _groq_request(prompt_messages, settings, stream=False)
    async with httpx.AsyncClient(timeout=httpx.Timeout(25.0)) as client:
        resp = await client.post(GROQ_BASE, headers=headers, json=body)
    if resp.status_code != 200:
        _raise_for_status(resp.status_code, resp.text, "groq")
    try:
        data = resp.json()
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise ProviderError("groq: undecodable response (%s)" % e, retryable=True)


async def _groq_stream(prompt_messages: list, settings):
    headers, body = _groq_request(prompt_messages, settings, stream=True)
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=120.0)) as client:
        async with client.stream("POST", GROQ_BASE, headers=headers, json=body) as resp:
            if resp.status_code != 200:
                text = await resp.aread()
                _raise_for_status(resp.status_code, text.decode("utf-8", "replace"), "groq")
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    data = json.loads(payload)
                    chunk = data["choices"][0]["delta"].get("content") or ""
                except (KeyError, IndexError, TypeError, ValueError):
                    continue
                if chunk:
                    yield chunk


def _local_request(prompt_messages: list, settings, stream: bool = False):
    # Ollama OpenAI-compatible endpoint. No auth header by default;
    # LOCAL_BASE may point at any OpenAI-style server on the LAN.
    body = {
        "model": settings.local_model,
        "messages": prompt_messages,
        "temperature": settings.temperature,
        "max_tokens": settings.max_output_tokens,
        "stream": stream,
    }
    return body


async def _local_text_once(prompt_messages: list, settings) -> str:
    body = _local_request(prompt_messages, settings, stream=False)
    # CPU inference on a small laptop is slow: allow a long first-token wait.
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=300.0)) as client:
        try:
            resp = await client.post(settings.local_base, json=body)
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as e:
            raise ProviderError("local: unreachable (%s)" % e, retryable=True)
    if resp.status_code != 200:
        _raise_for_status(resp.status_code, resp.text, "local")
    try:
        data = resp.json()
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise ProviderError("local: undecodable response (%s)" % e, retryable=True)


async def _local_stream(prompt_messages: list, settings):
    body = _local_request(prompt_messages, settings, stream=True)
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=300.0)) as client:
        try:
            async with client.stream("POST", settings.local_base, json=body) as resp:
                if resp.status_code != 200:
                    text = await resp.aread()
                    _raise_for_status(resp.status_code, text.decode("utf-8", "replace"), "local")
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload or payload == "[DONE]":
                        continue
                    try:
                        data = json.loads(payload)
                        chunk = data["choices"][0]["delta"].get("content") or ""
                    except (KeyError, IndexError, TypeError, ValueError):
                        continue
                    if chunk:
                        yield chunk
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as e:
            raise ProviderError("local: unreachable (%s)" % e, retryable=True)


def _enabled(settings) -> list:
    chain = []
    for name in settings.provider_order:
        if name == "gemini" and settings.gemini_api_key:
            chain.append("gemini")
        elif name == "groq" and settings.groq_api_key:
            chain.append("groq")
        elif name == "local":
            # No key needed: enabled whenever explicitly ordered.
            chain.append("local")
    return chain


async def generate_text(*, context: dict, text: str, settings) -> str:
    """Non-streaming answer. Tries providers in order; raises on exhaustion."""
    prompt_messages = build_messages(context, text, settings)
    chain = _enabled(settings)
    if not chain:
        raise ProviderConfigError()
    last_error: Exception = ProviderError("no provider attempted")
    for name in chain:
        try:
            if name == "gemini":
                return await _gemini_text(prompt_messages, settings)
            if name == "local":
                return await _local_text_once(prompt_messages, settings)
            return await _groq_text_once(prompt_messages, settings)
        except ProviderError as e:
            # Fall through everything, auth failures included: a bad key on
            # one provider must not sink a healthy other. The final error
            # keeps its type so HTTP mapping stays honest.
            log.warning("provider %s failed: %s", name, e)
            last_error = e
    raise last_error


async def generate_stream(*, context: dict, text: str, settings):
    """Yield text chunks from the first working provider (async generator)."""
    prompt_messages = build_messages(context, text, settings)
    chain = _enabled(settings)
    if not chain:
        raise ProviderConfigError()
    last_error: Exception = ProviderError("no provider attempted")
    for name in chain:
        try:
            if name == "gemini":
                async for chunk in _gemini_stream(prompt_messages, settings):
                    yield chunk
            elif name == "local":
                async for chunk in _local_stream(prompt_messages, settings):
                    yield chunk
            else:
                async for chunk in _groq_stream(prompt_messages, settings):
                    yield chunk
            return
        except ProviderError as e:
            # Same fall-through policy as generate_text (see above). Partial
            # chunks already sent keep their seq numbers; the fallback's
            # chunks continue the same sequence for the client.
            log.warning("provider %s stream failed: %s", name, e)
            last_error = e
    raise last_error
