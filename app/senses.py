"""Senses: vision, weather, system stats, server-side voice.

Optional perception layers for the hub. Every function degrades honestly:
a missing model/API becomes a typed error the routes map to 5xx — never a
fake answer. No new API keys needed (Open-Meteo is keyless; Ollama/Vosk are
local; psutil reads the local machine).
"""
from __future__ import annotations

import asyncio
import json

import httpx

OLLAMA_HOST = "http://localhost:11434"

# WMO weather codes (Open-Meteo) → plain words. Unknown codes stay honest.
WMO_DESCRIPTIONS = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Icy fog", 51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    56: "Freezing drizzle", 57: "Freezing drizzle", 61: "Light rain", 63: "Rain",
    65: "Heavy rain", 66: "Freezing rain", 67: "Freezing rain", 71: "Light snow",
    73: "Snow", 75: "Heavy snow", 77: "Snow grains", 80: "Light showers",
    81: "Showers", 82: "Violent showers", 85: "Light snow showers", 86: "Snow showers",
    95: "Thunderstorm", 96: "Storm with hail", 99: "Storm with hail",
}


def describe_wmo(code) -> str:
    try:
        return WMO_DESCRIPTIONS.get(int(code), "Unknown (code %s)" % code)
    except (TypeError, ValueError):
        return "Unknown"


async def fetch_weather(lat: float, lon: float) -> dict:
    """Current + today forecast from Open-Meteo (keyless). Raises on failure."""
    url = ("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s"
           "&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m"
           "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
           "&timezone=auto" % (lat, lon))
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.get(url)
    if resp.status_code != 200:
        raise RuntimeError("weather provider HTTP %d" % resp.status_code)
    try:
        data = resp.json()
        cur, daily = data["current"], data["daily"]
        code = cur["weather_code"]
        return {
            "temp_c": cur["temperature_2m"],
            "humidity": cur["relative_humidity_2m"],
            "wind_kph": cur["wind_speed_10m"],
            "code": code,
            "desc": describe_wmo(code),
            "today": {
                "max_c": daily["temperature_2m_max"][0],
                "min_c": daily["temperature_2m_min"][0],
                "precip_prob": daily["precipitation_probability_max"][0],
            },
        }
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise RuntimeError("weather provider undecodable (%s)" % e)


def system_stats() -> dict:
    """Local machine health (this laptop). psutil only, no network."""
    import psutil

    out: dict = {}
    try:
        out["cpu_pct"] = psutil.cpu_percent(interval=None)
    except Exception:
        out["cpu_pct"] = -1
    try:
        ram = psutil.virtual_memory()
        out["ram"] = {"pct": ram.percent,
                       "used_gb": round(ram.used / 1e9, 1),
                       "total_gb": round(ram.total / 1e9, 1)}
    except Exception:
        out["ram"] = None
    disks = {}
    for drive in ("A:\\", "C:\\"):
        try:
            du = psutil.disk_usage(drive)
            disks[drive] = {"pct": du.percent,
                            "free_gb": round(du.free / 1e9, 1),
                            "total_gb": round(du.total / 1e9, 1)}
        except Exception:
            pass
    out["disks"] = disks
    try:
        batt = psutil.sensors_battery()
        out["battery"] = ({"pct": round(batt.percent),
                            "plugged": bool(batt.power_plugged)} if batt else None)
    except Exception:
        out["battery"] = None
    try:
        import time as _time
        out["uptime_h"] = round((_time.time() - psutil.boot_time()) / 3600, 1)
    except Exception:
        out["uptime_h"] = -1
    return out


async def describe_image(image_b64: str, question: str, model: str,
                         host: str = OLLAMA_HOST) -> str:
    """Ask the local vision model about a base64 image (Ollama chat API).

    Raises LookupError when the vision model isn't pulled yet, ProviderError
    dict-style RuntimeError on model failure.
    """
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=300.0)) as client:
        resp = await client.post(host + "/api/chat", json={
            "model": model, "stream": False,
            "messages": [{"role": "user", "content": question, "images": [image_b64]}],
        })
    if resp.status_code == 404:
        raise LookupError("vision model '%s' not pulled — run: ollama pull %s" % (model, model))
    if resp.status_code != 200:
        raise RuntimeError("vision model HTTP %d" % resp.status_code)
    try:
        text = resp.json()["message"]["content"] or ""
    except (KeyError, TypeError, ValueError) as e:
        raise RuntimeError("vision model undecodable (%s)" % e)
    if not text.strip():
        raise RuntimeError("vision model returned empty text")
    return text


_VOSK_MODEL = None
_VOSK_MODEL_PATH = None


def _load_vosk(model_dir: str):
    global _VOSK_MODEL, _VOSK_MODEL_PATH
    if _VOSK_MODEL is not None and _VOSK_MODEL_PATH == model_dir:
        return _VOSK_MODEL
    import os

    if not os.path.isdir(model_dir):
        raise FileNotFoundError("voice model not downloaded: %s" % model_dir)
    from vosk import Model

    _VOSK_MODEL = Model(model_dir)
    _VOSK_MODEL_PATH = model_dir
    return _VOSK_MODEL


def _transcribe_sync(data: bytes, sample_rate: int, model_dir: str) -> str:
    from vosk import KaldiRecognizer

    model = _load_vosk(model_dir)
    rec = KaldiRecognizer(model, sample_rate)
    rec.SetWords(False)
    step = 8000
    for i in range(0, len(data), step):
        rec.AcceptWaveform(data[i:i + step])
    try:
        text = json.loads(rec.FinalResult()).get("text", "")
    except ValueError:
        text = ""
    return text


async def transcribe_pcm16(data: bytes, sample_rate: int = 16000, model_dir: str = "") -> str:
    """Speech-to-text for 16-bit mono PCM bytes (Vosk, local, free).

    Clients must send raw PCM16 mono (queues/rate noted in README). The
    blocking recognizer runs in a thread so the event loop never stalls.
    """
    if not data:
        raise ValueError("empty audio")
    return await asyncio.to_thread(_transcribe_sync, data, sample_rate, model_dir)
