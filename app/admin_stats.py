"""In-memory admin telemetry for the local ZYRAXIS server.

No database — everything resets on restart, which is fine for a laptop
server. Single event loop, so plain counters are safe.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class AdminStats:
    started: float = field(default_factory=time.time)
    rest_hits: int = 0
    rest_errors: int = 0
    ws_connects: int = 0
    ws_current: int = 0
    ws_messages: int = 0
    llm_ok: int = 0
    llm_fail: int = 0
    per_ip: dict = field(default_factory=dict)
    devices: dict = field(default_factory=dict)
    events: deque = field(default_factory=lambda: deque(maxlen=50))
    transcript: deque = field(default_factory=lambda: deque(maxlen=30))
    peers: dict = field(default_factory=dict)
    pending: dict = field(default_factory=dict)
    ping_sent: dict = field(default_factory=dict)

    def hit_ip(self, ip: str) -> None:
        self.per_ip[ip] = self.per_ip.get(ip, 0) + 1

    def event(self, text: str) -> None:
        self.events.appendleft(
            {"t": time.strftime("%H:%M:%S", time.localtime()), "msg": str(text)[:200]}
        )

    def uptime(self) -> str:
        secs = int(time.time() - self.started)
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        return "%dh %dm %ds" % (h, m, s)

    def device_seen(self, session_id: str, ip: str, info: dict) -> None:
        self.devices[str(session_id)[:64]] = {
            "ip": ip,
            "model": str(info.get("clientModel") or "?")[:40],
            "app": str(info.get("appVersion") or "?")[:20],
            "last": time.strftime("%H:%M:%S", time.localtime()),
        }

    def peer_name(self, ws) -> str | None:
        for sid, peer in self.peers.items():
            sock = peer[0] if isinstance(peer, tuple) else peer
            if sock is ws:
                if isinstance(peer, tuple):
                    return peer[1]
                return sid
        return None

    def peer_socket(self, sid: str):
        peer = self.peers.get(sid)
        if isinstance(peer, tuple):
            return peer[0]
        return peer

    def record_turn(self, device: str, text: str, reply: str) -> None:
        self.transcript.appendleft({
            "t": time.strftime("%H:%M:%S", time.localtime()),
            "dev": str(device)[:40],
            "q": str(text)[:500],
            "a": str(reply)[:500],
        })

    def queue_cmd(self, device: str, command: str) -> None:
        self.pending.setdefault(device, []).append(str(command)[:200])

    def pop_cmd(self, device: str):
        q = self.pending.get(device) or []
        return q.pop(0) if q else None


class StatsLogHandler(logging.Handler):
    """Mirrors app log records into the admin event feed (no bodies/keys)."""

    def __init__(self, stats: AdminStats) -> None:
        super().__init__(level=logging.INFO)
        self._stats = stats

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._stats.event(record.getMessage())
        except Exception:
            pass
