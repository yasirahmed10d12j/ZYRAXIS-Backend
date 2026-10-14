"""Wire protocol builders mirroring ZYRAXIS/Networking/BackendProtocol.swift.

Every frame: {version, type, requestId, payload}. Version is always 1.
requestId is echoed verbatim from the client's message (must stay a UUID
string or the iOS adapter drops the frame).
"""
from __future__ import annotations

PROTOCOL_VERSION = 1


def envelope(msg_type: str, request_id: str, payload: dict | None = None) -> dict:
    return {
        "version": PROTOCOL_VERSION,
        "type": msg_type,
        "requestId": request_id,
        "payload": payload or {},
    }


def assistant_delta(request_id: str, delta: str, seq: int) -> dict:
    # seq is 1-based per request; the client drops duplicates/stale chunks.
    return envelope("assistant_delta", request_id, {"delta": delta, "seq": seq})


def assistant_complete(request_id: str, full_text: str, tool_calls: list | None = None) -> dict:
    return envelope(
        "assistant_complete",
        request_id,
        {"fullText": full_text, "toolCalls": tool_calls or []},
    )


def assistant_error(request_id: str, code: str, message: str, retryable: bool = False) -> dict:
    return envelope(
        "assistant_error",
        request_id,
        {"code": code, "message": message, "retryable": retryable},
    )


def pong_frame(request_id: str, timestamp: float) -> dict:
    return envelope("pong", request_id, {"timestamp": timestamp})


def tool_call_frame(request_id: str, tool_call: dict, seq: int = 0) -> dict:
    # v0.1 server never sends tool_call; builder kept for protocol completeness.
    return envelope("tool_call", request_id, {"toolCall": tool_call, "seq": seq})


def remote_command_frame(request_id: str, command: str) -> dict:
    # Server → device push. Current iOS builds tolerate + ignore unknown
    # types, so this is harmless until the client adds a receiver that
    # routes `command` through its fast-command router.
    return envelope("remote_command", request_id, {"command": command})
