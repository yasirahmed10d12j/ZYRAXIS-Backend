"""ZYRAXIS backend — FastAPI service implementing the exact iOS wire contract.

iOS contract sources (D:\\Projects\\ZYRAXIS-iOS):
- REST   POST /api/{apiVersion}/chat  (BackendAIService.swift:98-103)
- WS     envelopes {version,type,requestId,payload} (BackendProtocol.swift:114-151)
- Auth   Authorization: Bearer <user key> on REST (BackendAIService.swift:267-274);
         the iOS socket opens with NO auth headers (WebSocketManager.swift:80-92),
         so WS auth is validate-if-present (see auth.check_ws_key).
- Engine rules echoed here: UUID requestId echo (adapter requires it),
  1-based strictly-increasing delta seq (ConversationManager.swift:234),
  complete carries fullText (falls back to accumulated deltas).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid

from fastapi import FastAPI, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import httpx

from . import providers as llm
from .admin_stats import AdminStats, StatsLogHandler
from .adminpage import render as render_admin
from .auth import AuthError, check_bearer, check_ws_key
from .config import get_settings
from .syncstore import MemoryStore, TaskStore
from .protocol import (
    PROTOCOL_VERSION,
    assistant_complete,
    assistant_delta,
    assistant_error,
    pong_frame,
    remote_command_frame,
)
from .ratelimit import RateLimiter

log = logging.getLogger("zyraxis")
logging.basicConfig(level=logging.INFO)

settings = get_settings()
limiter = RateLimiter()
stats = AdminStats()
logging.getLogger("zyraxis").addHandler(StatsLogHandler(stats))

# Shared ecosystem stores (JSON on disk so a restart keeps everything).
# Paths are env-overridable for tests; defaults live on Drive A:.
import os as _os

memory_store = MemoryStore(_os.environ.get("MEMORY_FILE", r"A:\ZYRAXIS-Server\hub\memory.json"))
task_store = TaskStore(_os.environ.get("TASKS_FILE", r"A:\ZYRAXIS-Server\hub\tasks.json"))

# Largest accepted REST body (1 MiB). Starlette reads bodies unbounded by
# default; on a 512 MB free instance an unbounded read is a trivial DoS.
# WebSocket frames are already capped by the server's 1 MiB message limit.
MAX_BODY_BYTES = 1048576

app = FastAPI(title="ZYRAXIS backend", version="0.1.0")


def _client_ip(headers, client) -> str:
    try:
        fwd = (headers.get("x-forwarded-for") or "")
        if fwd:
            return fwd.split(",")[0].strip()
    except Exception:
        pass
    try:
        return client.host
    except Exception:
        return "unknown"


@app.get("/")
async def root():
    # Doubles as a warmup hit (Render spins up on HTTP).
    return {"service": "zyraxis-backend", "version": "0.1.0", "protocol": PROTOCOL_VERSION}


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.post("/api/{api_version}/chat")
async def chat(api_version: str, request: Request):
    ip = _client_ip(request.headers, request.client)
    stats.hit_ip(ip)
    if not limiter.check_rest(ip):
        stats.rest_errors += 1
        return JSONResponse({"detail": "rate limited, retry later"}, status_code=429)
    try:
        check_bearer(request.headers.get("authorization"), settings.backend_keys)
    except AuthError as e:
        return JSONResponse({"detail": str(e)}, status_code=401)
    try:
        raw = await request.body()
    except Exception:
        return JSONResponse({"detail": "unreadable body"}, status_code=400)
    if len(raw) > MAX_BODY_BYTES:
        return JSONResponse({"detail": "body too large"}, status_code=413)
    try:
        body = json.loads(raw.decode("utf-8"))
    except ValueError:
        return JSONResponse({"detail": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"detail": "invalid JSON body"}, status_code=400)
    text = body.get("text", "")
    if not isinstance(text, str) or not text.strip():
        return JSONResponse({"detail": "missing 'text'"}, status_code=400)
    if api_version != settings.api_version:
        log.info("api-version chap=%s server=%s (serving anyway)", api_version, settings.api_version)
    context = body.get("context") or {}
    if not isinstance(context, dict):
        context = {}
    req_id = body.get("requestId") or str(uuid.uuid4())
    try:
        answer = await llm.generate_text(context=context, text=text, settings=settings)
        stats.rest_hits += 1
        stats.llm_ok += 1
    except llm.RateLimitedError:
        stats.rest_errors += 1
        stats.llm_fail += 1
        return JSONResponse({"detail": "provider rate limited"}, status_code=429)
    except llm.ProviderAuthError:
        stats.rest_errors += 1
        stats.llm_fail += 1
        return JSONResponse({"detail": "provider credentials rejected"}, status_code=502)
    except llm.ProviderError:
        stats.rest_errors += 1
        stats.llm_fail += 1
        return JSONResponse({"detail": "provider failure"}, status_code=502)
    log.info("chat rid=%s chars=%d", req_id, len(answer))
    stats.record_turn(ip, text, answer)
    return {"text": answer, "toolCalls": [], "requestId": req_id}


async def _send(websocket: WebSocket, frame: dict) -> None:
    try:
        await websocket.send_text(json.dumps(frame))
    except (WebSocketDisconnect, RuntimeError):
        pass


async def _run_turn(websocket: WebSocket, rid: str, text: str, context: dict, ip: str) -> None:
    if not isinstance(text, str) or not text.strip():
        await _send(websocket, assistant_error(rid, "EMPTY_MESSAGE", "empty message", False))
        return
    device = stats.peer_name(websocket) or ip
    seq = 0
    chunks: list = []
    try:
        async for piece in llm.generate_stream(context=context, text=text, settings=settings):
            if not piece:
                continue
            seq += 1
            chunks.append(piece)
            await _send(websocket, assistant_delta(rid, piece, seq))
    except asyncio.CancelledError:
        return  # superseded/cancelled: client already moved on; stay silent
    except llm.RateLimitedError:
        stats.llm_fail += 1
        await _send(websocket, assistant_error(rid, "RATE_LIMITED", "provider rate limited, retry later", True))
        return
    except llm.ProviderAuthError:
        stats.llm_fail += 1
        await _send(
            websocket, assistant_error(rid, "PROVIDER_ERROR", "provider credentials rejected", False)
        )
        return
    except llm.ProviderError:
        stats.llm_fail += 1
        await _send(websocket, assistant_error(rid, "PROVIDER_ERROR", "provider failure", True))
        return
    stats.llm_ok += 1
    full = "".join(chunks)
    stats.record_turn(device, text, full)
    await _send(websocket, assistant_complete(rid, full, []))


@app.websocket("/live")
async def live(websocket: WebSocket):
    await websocket.accept()
    ip = _client_ip(websocket.headers, websocket.client)
    if not limiter.check_ws_connect(ip):
        await websocket.close(code=1013)
        return
    if not check_ws_key(websocket.query_params.get("key"), settings.backend_keys):
        await websocket.close(code=1008, reason="unauthorized")
        return
    stats.ws_connects += 1
    stats.ws_current += 1
    stats.hit_ip(ip)
    stats.event("ws open %s" % ip)
    gen_task: asyncio.Task | None = None
    try:
        while True:
            try:
                raw = await websocket.receive_text()
            except WebSocketDisconnect:
                break
            stats.ws_messages += 1
            if not limiter.check_ws_message(ip):
                continue  # drop flood, keep socket
            try:
                msg = json.loads(raw)
            except ValueError:
                continue  # malformed frame: tolerate + ignore
            if not isinstance(msg, dict):
                continue
            if msg.get("version") != PROTOCOL_VERSION:
                rid = msg.get("requestId")
                if isinstance(rid, str) and rid:
                    await _send(
                        websocket,
                        assistant_error(rid, "VERSION_MISMATCH", "protocol mismatch", False),
                    )
                continue
            mtype = msg.get("type")
            rid = msg.get("requestId")
            if not isinstance(rid, str) or not rid:
                continue  # cannot route a reply; ignore
            payload = msg.get("payload") or {}
            if not isinstance(payload, dict):
                payload = {}
            if mtype == "session_start":
                # Client self-emits .connected; no ack frame defined. But the
                # handshake carries device identity — record it for /admin.
                # Newer clients send stable deviceId/deviceName (one phone =
                # one device across turns); older ones fall back to the
                # per-turn session id (previous behavior, unchanged).
                sid = payload.get("sessionId") or rid
                did = str(payload.get("deviceId") or sid)[:64]
                name = str(payload.get("deviceName") or payload.get("clientModel") or "?")[:40]
                prev = stats.devices.get(did, {})
                sessions = list(prev.get("sessions", []))
                if sid not in sessions:
                    sessions.append(sid)
                    sessions = sessions[-5:]
                stats.devices[did] = {
                    "ip": ip,
                    "name": name,
                    "model": str(payload.get("clientModel") or "?")[:40],
                    "app": str(payload.get("appVersion") or "?")[:20],
                    "last": time.strftime("%H:%M:%S", time.localtime()),
                    "sessions": sessions,
                }
                stats.peers[sid] = (websocket, did)
                stats.event("device %s %s" % (ip, name))
                queued = stats.pop_cmd(did)
                if queued is not None:
                    await _send(websocket, remote_command_frame(str(uuid.uuid4()), queued))
                    stats.event("flushed queued cmd to %s" % did)
                continue  # client self-emits .connected; no ack frame defined
            if mtype == "user_message":
                if gen_task is not None and not gen_task.done():
                    gen_task.cancel()
                text = payload.get("text", "")
                context = payload.get("context") or {}
                if not isinstance(context, dict):
                    context = {}
                gen_task = asyncio.create_task(_run_turn(websocket, rid, text, context, ip))
                continue
            if mtype == "session_end":
                if gen_task is not None and not gen_task.done():
                    gen_task.cancel()
                continue
            if mtype == "ping":
                ts = payload.get("timestamp", time.time())
                if not isinstance(ts, (int, float)):
                    ts = time.time()
                await _send(websocket, pong_frame(rid, ts))
                continue
            if mtype == "pong":
                # Reply to a dashboard-initiated ping (see /admin/ping).
                sent = stats.ping_sent.pop(rid, None)
                ms = (time.time() - sent) * 1000 if sent else -1
                stats.event("pong %s %dms" % (stats.peer_name(websocket) or ip, int(ms)))
                continue
            if mtype == "tool_result":
                # v0.1 never sends tool_call, so this is unexpected; tolerate.
                log.info("tool_result rid=%s tool=%s", rid, payload.get("toolName"))
                continue
            # Unknown types tolerated + ignored (forward compatibility).
    finally:
        if gen_task is not None and not gen_task.done():
            gen_task.cancel()
        limiter.release_ws(ip)
        stats.ws_current = max(0, stats.ws_current - 1)
        dead = [sid for sid in list(stats.peers) if stats.peer_socket(sid) is websocket]
        for sid in dead:
            del stats.peers[sid]
        stats.event("ws close %s" % ip)


def _admin_key(request: Request) -> bool:
    # Browser-friendly: ?key=... or Authorization header. Fail-closed:
    # missing or wrong key is always denied (unlike the WS socket).
    key = request.query_params.get("key")
    if not key:
        _, _, token = (request.headers.get("authorization") or "").partition(" ")
        key = token.strip() or None
    return bool(key) and bool(settings.backend_keys) and key in settings.backend_keys


async def _ollama_status() -> tuple:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(3.0)) as client:
            resp = await client.get("http://localhost:11434/api/tags")
        if resp.status_code != 200:
            return False, []
        names = [m.get("name", "?") for m in resp.json().get("models", [])]
        return True, names
    except Exception:
        return False, []


def _lan_ip() -> str:
    # Outbound interface address without sending traffic (UDP connect does
    # no I/O). Falls back to "unavailable" offline — recomputed per page
    # load because hotspot IPs change on restart.
    import socket as _socket

    try:
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    return "unavailable"


def _connect_info() -> dict:
    import os as _os

    port = _os.environ.get("PUBLIC_PORT", "8000").strip() or "8000"
    ip = _lan_ip()
    keys = sorted(settings.backend_keys)
    return {
        "ip": ip,
        "base": "http://%s:%s" % (ip, port) if ip != "unavailable" else "unavailable",
        "ws": "ws://%s:%s/live" % (ip, port) if ip != "unavailable" else "unavailable",
        # Shown only to key-holding admins (this page is 401 without it),
        # so displaying one key adds no new exposure — and makes onboarding
        # a new iPhone a copy-paste job.
        "key": keys[0] if keys else "NOT SET — server denies everything",
    }


@app.get("/admin")
async def admin(request: Request):
    if not _admin_key(request):
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    ok, models = await _ollama_status()
    key = request.query_params.get("key", "")
    hub = {"memories": len(memory_store.items), "tasks": len(task_store.list("active")),
           "sys": _system_stats()}
    return HTMLResponse(render_admin(stats, settings, ok, models, key, _connect_info(), hub))


@app.post("/admin/config")
async def admin_config(
    request: Request,
    provider_order: str = Form(""),
    max_output_tokens: str = Form("1024"),
    temperature: str = Form("0.7"),
    key: str = Form(""),
):
    global settings
    from dataclasses import replace

    # Form-posted key counts too (browsers can't set headers on plain forms).
    if not _admin_key(request) and key not in settings.backend_keys:
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    order = tuple(p.strip().lower() for p in provider_order.split(",") if p.strip().lower() in ("local", "gemini", "groq"))
    try:
        max_tokens = max(64, min(4096, int(max_output_tokens)))
    except ValueError:
        max_tokens = settings.max_output_tokens
    try:
        temp = max(0.0, min(1.0, float(temperature)))
    except ValueError:
        temp = settings.temperature
    settings = replace(
        settings,
        provider_order=order or settings.provider_order,
        max_output_tokens=max_tokens,
        temperature=temp,
    )
    stats.event("config updated: order=%s max=%d temp=%.2f" % (",".join(settings.provider_order), max_tokens, temp))
    show = key or request.query_params.get("key", "")
    return RedirectResponse("/admin?key=%s" % show if show else "/admin", status_code=303)


def _redirect_back(key: str):
    return RedirectResponse("/admin?key=%s" % key if key else "/admin", status_code=303)


@app.get("/admin/export")
async def admin_export(request: Request):
    if not _admin_key(request):
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    lines = ["ZYRAXIS transcript export"]
    for t in list(stats.transcript)[::-1]:
        lines.append("[%s] %s | Q: %s" % (t["t"], t["dev"], t["q"]))
        lines.append("[%s] %s | A: %s" % (t["t"], t["dev"], t["a"]))
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse("\n".join(lines) + "\n",
                             headers={"Content-Disposition": "attachment; filename=zyraxis-transcript.txt"})


@app.post("/admin/broadcast")
async def admin_broadcast(request: Request, command: str = Form(""), key: str = Form("")):
    if not _admin_key(request) and key not in settings.backend_keys:
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    show = key or request.query_params.get("key", "")
    command = command.strip()[:200]
    if not command:
        return _redirect_back(show)
    sent, routes = 0, set()
    for sid in list(stats.peers):
        peer = stats.peer_socket(sid)
        if peer is None:
            continue
        try:
            await peer.send_text(json.dumps(remote_command_frame(str(uuid.uuid4()), command)))
            sent += 1
        except (WebSocketDisconnect, RuntimeError):
            pass
    stats.event("broadcast '%s' -> %d device(s)" % (command, sent))
    return _redirect_back(show)


@app.post("/admin/model-unload")
async def admin_model_unload(request: Request, key: str = Form("")):
    if not _admin_key(request) and key not in settings.backend_keys:
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    show = key or request.query_params.get("key", "")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            resp = await client.post("http://localhost:11434/api/generate",
                                     json={"model": settings.local_model, "keep_alive": 0})
        if resp.status_code == 200:
            stats.event("model unloaded from RAM (%s)" % settings.local_model)
        else:
            stats.event("unload failed: HTTP %d" % resp.status_code)
    except Exception as e:
        stats.event("unload failed: %s" % str(e)[:100])
    return _redirect_back(show)


@app.post("/admin/note")
async def admin_note(request: Request, content: str = Form(""), key: str = Form("")):
    if not _admin_key(request) and key not in settings.backend_keys:
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    show = key or request.query_params.get("key", "")
    content = content.strip()[:2000]
    if content:
        try:
            memory_store.save(content, ["dashboard"], 0.6)
            stats.event("note saved to shared memory")
        except ValueError:
            pass
    return _redirect_back(show)


@app.post("/admin/task")
async def admin_task(request: Request, title: str = Form(""), key: str = Form("")):
    if not _admin_key(request) and key not in settings.backend_keys:
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    show = key or request.query_params.get("key", "")
    title = title.strip()[:300]
    if title:
        try:
            task_store.create(title)
            stats.event("task added: %s" % title[:80])
        except ValueError:
            pass
    return _redirect_back(show)


@app.post("/admin/weather")
async def admin_weather(request: Request, lat: str = Form(""), lon: str = Form(""), key: str = Form("")):
    if not _admin_key(request) and key not in settings.backend_keys:
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    show = key or request.query_params.get("key", "")
    try:
        la, lo = float(lat), float(lon)
        if not (-90 <= la <= 90 and -180 <= lo <= 180):
            raise ValueError("range")
        data = await _fetch_weather(la, lo)
        data["ok"] = True
        stats.last_weather = data
        stats.event("weather checked %.2f,%.2f" % (la, lo))
    except ValueError:
        stats.last_weather = {"ok": False, "error": "bad coordinates"}
    except Exception as e:
        stats.last_weather = {"ok": False, "error": str(e)[:100]}
    return _redirect_back(show)


@app.post("/admin/vision")
async def admin_vision(request: Request, image: str = Form(""), question: str = Form(""), key: str = Form("")):
    if not _admin_key(request) and key not in settings.backend_keys:
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    show = key or request.query_params.get("key", "")
    if not image.strip():
        stats.last_vision = "No image received."
    elif len(image) > 11 * 1048576:
        stats.last_vision = "Image too large (8 MiB cap)."
    else:
        try:
            stats.last_vision = await _describe_image(
                image, (question or "Describe this image briefly.")[:500],
                settings.vision_model, settings.ollama_host)
            stats.event("vision described an image")
        except LookupError as e:
            stats.last_vision = str(e)
        except Exception:
            stats.last_vision = "Vision model failure."
    return _redirect_back(show)


def _device_socket(device: str):
    # Direct session id first (live socket), then stable device id via its
    # known sessions (newest first). Lets the dashboard target a device
    # across turns instead of a single expiring session.
    sock = stats.peer_socket(device)
    if sock is not None:
        return sock
    info = stats.devices.get(device) or {}
    for sid in list(info.get("sessions", []))[::-1]:
        sock = stats.peer_socket(sid)
        if sock is not None:
            return sock
    return None


@app.post("/admin/cmd")
async def admin_cmd(request: Request, device: str = Form(""), command: str = Form(""), key: str = Form("")):
    if not _admin_key(request) and key not in settings.backend_keys:
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    show = key or request.query_params.get("key", "")
    command = command.strip()[:200]
    if not device or not command:
        return _redirect_back(show)
    peer = _device_socket(device)
    if peer is not None:
        try:
            await peer.send_text(json.dumps(remote_command_frame(str(uuid.uuid4()), command)))
            stats.event("remote cmd -> %s: %s" % (device, command))
        except (WebSocketDisconnect, RuntimeError):
            stats.queue_cmd(device, command)
            stats.event("device gone, queued for %s: %s" % (device, command))
    else:
        stats.queue_cmd(device, command)
        stats.event("queued for offline %s: %s (runs on next session_start)" % (device, command))
    return _redirect_back(show)


@app.post("/admin/ping")
async def admin_ping(request: Request, device: str = Form(""), key: str = Form("")):
    if not _admin_key(request) and key not in settings.backend_keys:
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    show = key or request.query_params.get("key", "")
    peer = _device_socket(device)
    if peer is not None:
        rid = str(uuid.uuid4())
        stats.ping_sent[rid] = time.time()
        try:
            await peer.send_text(json.dumps(
                {"version": PROTOCOL_VERSION, "type": "ping",
                 "requestId": rid, "payload": {"timestamp": time.time()}}))
            stats.event("ping -> %s (watch for pong)" % device)
        except (WebSocketDisconnect, RuntimeError):
            stats.event("ping failed, %s offline" % device)
    else:
        stats.event("ping failed, %s offline" % device)
    return _redirect_back(show)


@app.post("/admin/clear")
async def admin_clear(request: Request, key: str = Form("")):
    if not _admin_key(request) and key not in settings.backend_keys:
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    show = key or request.query_params.get("key", "")
    stats.transcript.clear()
    stats.events.clear()
    stats.event("dashboard cleared by admin")
    return _redirect_back(show)


def _require_key(request: Request):
    try:
        check_bearer(request.headers.get("authorization"), settings.backend_keys)
        return None
    except AuthError as e:
        return JSONResponse({"detail": str(e)}, status_code=401)


# MARK: - Ecosystem sync API (shared memory + tasks + devices).
#
# Every client uses the same Bearer key as chat. Bodies are small JSON;
# responses are plain JSON (no envelopes here — envelopes are WS-only).


@app.get("/api/{api_version}/devices")
async def list_devices(api_version: str, request: Request):
    denied = _require_key(request)
    if denied is not None:
        return denied
    out = []
    for did, d in stats.devices.items():
        out.append({
            "id": did,
            "name": d.get("name", "?"),
            "ip": d.get("ip", "?"),
            "model": d.get("model", "?"),
            "app": d.get("app", "?"),
            "online": any(s in stats.peers for s in d.get("sessions", [])),
            "sessions": len(d.get("sessions", [])),
            "last": d.get("last", ""),
        })
    out.sort(key=lambda d: d["last"], reverse=True)
    return {"devices": out}


@app.get("/api/{api_version}/memory")
async def memory_search(api_version: str, request: Request):
    denied = _require_key(request)
    if denied is not None:
        return denied
    try:
        limit = max(1, min(100, int(request.query_params.get("limit", "20"))))
    except ValueError:
        limit = 20
    return {"memories": memory_store.search(request.query_params.get("query", ""), limit)}


@app.post("/api/{api_version}/memory")
async def memory_save(api_version: str, request: Request):
    denied = _require_key(request)
    if denied is not None:
        return denied
    try:
        body = json.loads((await request.body()).decode("utf-8"))
    except ValueError:
        return JSONResponse({"detail": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"detail": "invalid JSON body"}, status_code=400)
    try:
        item = memory_store.save(
            body.get("content", ""),
            body.get("tags") or [],
            body.get("importance", 0.5),
        )
    except (ValueError, TypeError):
        return JSONResponse({"detail": "missing 'content'"}, status_code=400)
    return item


@app.delete("/api/{api_version}/memory/{item_id}")
async def memory_forget(api_version: str, item_id: str, request: Request):
    denied = _require_key(request)
    if denied is not None:
        return denied
    if not memory_store.forget(item_id):
        return JSONResponse({"detail": "not found"}, status_code=404)
    return {"forgotten": item_id}


@app.get("/api/{api_version}/tasks")
async def tasks_list(api_version: str, request: Request):
    denied = _require_key(request)
    if denied is not None:
        return denied
    filt = (request.query_params.get("filter", "active") or "active").lower()
    if filt not in ("active", "all", "completed", "cancelled"):
        filt = "active"
    return {"tasks": task_store.list(filt)}


@app.post("/api/{api_version}/tasks")
async def tasks_create(api_version: str, request: Request):
    denied = _require_key(request)
    if denied is not None:
        return denied
    try:
        body = json.loads((await request.body()).decode("utf-8"))
    except ValueError:
        return JSONResponse({"detail": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"detail": "invalid JSON body"}, status_code=400)
    try:
        item = task_store.create(
            body.get("title", ""),
            body.get("notes"),
            body.get("dueDate"),
            body.get("priority", "medium"),
        )
    except (ValueError, TypeError):
        return JSONResponse({"detail": "missing 'title'"}, status_code=400)
    return item


@app.post("/api/{api_version}/tasks/{task_id}/complete")
async def tasks_complete(api_version: str, task_id: str, request: Request):
    denied = _require_key(request)
    if denied is not None:
        return denied
    item = task_store.complete(task_id)
    if item is None:
        return JSONResponse({"detail": "not found"}, status_code=404)
    return item


@app.post("/api/{api_version}/tasks/{task_id}/cancel")
async def tasks_cancel(api_version: str, task_id: str, request: Request):
    denied = _require_key(request)
    if denied is not None:
        return denied
    item = task_store.cancel(task_id)
    if item is None:
        return JSONResponse({"detail": "not found"}, status_code=404)
    return item


@app.post("/api/{api_version}/tasks/{task_id}/snooze")
async def tasks_snooze(api_version: str, task_id: str, request: Request):
    denied = _require_key(request)
    if denied is not None:
        return denied
    try:
        body = json.loads((await request.body()).decode("utf-8"))
    except ValueError:
        return JSONResponse({"detail": "invalid JSON body"}, status_code=400)
    until = body.get("until") if isinstance(body, dict) else None
    if not until:
        return JSONResponse({"detail": "missing 'until'"}, status_code=400)
    item = task_store.snooze(task_id, str(until))
    if item is None:
        return JSONResponse({"detail": "not found"}, status_code=404)
    return item


# MARK: - Senses (vision, weather, system, voice). All optional layers;
# every failure is an honest 5xx, never a fabricated answer.
from .senses import describe_image as _describe_image
from .senses import describe_wmo as _describe_wmo  # noqa: F401 (dashboard/tests)
from .senses import fetch_weather as _fetch_weather
from .senses import system_stats as _system_stats
from .senses import transcribe_pcm16 as _transcribe_pcm16


@app.post("/api/{api_version}/vision")
async def vision_describe(api_version: str, request: Request):
    denied = _require_key(request)
    if denied is not None:
        return denied
    try:
        raw = await request.body()
    except Exception:
        return JSONResponse({"detail": "unreadable body"}, status_code=400)
    if len(raw) > 8 * 1048576:
        return JSONResponse({"detail": "image too large (8 MiB cap)"}, status_code=413)
    try:
        body = json.loads(raw.decode("utf-8"))
    except ValueError:
        return JSONResponse({"detail": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict) or not body.get("image"):
        return JSONResponse({"detail": "missing 'image' (base64)"}, status_code=400)
    question = str(body.get("question") or "Describe this image briefly.")[:500]
    try:
        text = await _describe_image(str(body["image"]), question,
                                     settings.vision_model, settings.ollama_host)
    except LookupError as e:
        return JSONResponse({"detail": str(e)}, status_code=503)
    except (RuntimeError, Exception):
        return JSONResponse({"detail": "vision model failure"}, status_code=502)
    return {"text": text, "model": settings.vision_model}


@app.get("/api/{api_version}/weather")
async def weather_now(api_version: str, request: Request):
    denied = _require_key(request)
    if denied is not None:
        return denied
    try:
        lat = float(request.query_params.get("lat", ""))
        lon = float(request.query_params.get("lon", ""))
    except (TypeError, ValueError):
        return JSONResponse({"detail": "lat/lon required (decimal degrees)"}, status_code=400)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return JSONResponse({"detail": "lat/lon out of range"}, status_code=400)
    try:
        return await _fetch_weather(lat, lon)
    except Exception:
        return JSONResponse({"detail": "weather provider failure"}, status_code=502)


@app.get("/api/{api_version}/system")
async def system_health(api_version: str, request: Request):
    denied = _require_key(request)
    if denied is not None:
        return denied
    return _system_stats()


@app.post("/api/{api_version}/transcribe")
async def transcribe(api_version: str, request: Request):
    denied = _require_key(request)
    if denied is not None:
        return denied
    try:
        raw = await request.body()
    except Exception:
        return JSONResponse({"detail": "unreadable body"}, status_code=400)
    if len(raw) > 5 * 1048576:
        return JSONResponse({"detail": "audio too large (5 MiB cap)"}, status_code=413)
    try:
        body = json.loads(raw.decode("utf-8"))
    except ValueError:
        return JSONResponse({"detail": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict) or not body.get("audio"):
        return JSONResponse({"detail": "missing 'audio' (base64 PCM16 mono)"}, status_code=400)
    import base64

    try:
        pcm = base64.b64decode(body["audio"], validate=True)
    except Exception:
        return JSONResponse({"detail": "audio is not valid base64"}, status_code=400)
    try:
        rate = int(body.get("sample_rate", 16000))
    except (TypeError, ValueError):
        return JSONResponse({"detail": "bad sample_rate"}, status_code=400)
    if rate not in (8000, 16000, 44100, 48000):
        return JSONResponse({"detail": "sample_rate must be 8000/16000/44100/48000"}, status_code=400)
    try:
        text = await _transcribe_pcm16(pcm, rate, settings.vosk_model_dir)
    except FileNotFoundError as e:
        return JSONResponse({"detail": str(e)}, status_code=503)
    except (ValueError, Exception):
        return JSONResponse({"detail": "transcription failure"}, status_code=502)
    return {"text": text}
