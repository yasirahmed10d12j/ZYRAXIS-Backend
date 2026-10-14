"""Environment-driven settings. No secrets in code — see .env.example."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    backend_keys: frozenset = frozenset()
    gemini_api_key: str = ""
    groq_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash-lite"
    groq_model: str = "llama-3.1-8b-instant"
    local_model: str = "qwen2.5:3b"
    local_base: str = "http://localhost:11434/v1/chat/completions"
    provider_order: tuple = ("gemini", "groq")
    api_version: str = "v1"
    system_prompt: str = (
        "You are ZYRAXIS, a concise futuristic AI assistant. "
        "Answer helpfully in plain text. Keep replies short unless asked for detail."
    )
    max_output_tokens: int = 1024
    temperature: float = 0.7


def _csv(name: str) -> list:
    raw = os.environ.get(name, "")
    return [p.strip() for p in raw.split(",") if p.strip()]


def get_settings() -> Settings:
    order = [p.lower() for p in _csv("PROVIDER_ORDER")] or ["gemini", "groq"]
    order = tuple(p for p in order if p in ("gemini", "groq", "local")) or ("gemini", "groq")
    try:
        max_tokens = int(os.environ.get("MAX_OUTPUT_TOKENS", "1024"))
    except ValueError:
        max_tokens = 1024
    try:
        temperature = float(os.environ.get("TEMPERATURE", "0.7"))
    except ValueError:
        temperature = 0.7
    return Settings(
        backend_keys=frozenset(_csv("BACKEND_API_KEYS")),
        gemini_api_key=os.environ.get("GEMINI_API_KEY", "").strip(),
        groq_api_key=os.environ.get("GROQ_API_KEY", "").strip(),
        gemini_model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite").strip(),
        groq_model=os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant").strip(),
        local_model=os.environ.get("LOCAL_MODEL", "qwen2.5:3b").strip() or "qwen2.5:3b",
        local_base=os.environ.get("LOCAL_BASE", "http://localhost:11434/v1/chat/completions").strip()
        or "http://localhost:11434/v1/chat/completions",
        provider_order=order,
        api_version=os.environ.get("API_VERSION", "v1").strip() or "v1",
        system_prompt=os.environ.get("SYSTEM_PROMPT", "").strip()
        or Settings.system_prompt,
        max_output_tokens=max_tokens,
        temperature=temperature,
    )
