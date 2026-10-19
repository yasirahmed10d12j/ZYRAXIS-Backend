"""Tiny in-memory abuse guard. No DB, no state worth persisting."""
from __future__ import annotations

import time


class RateLimiter:
    def __init__(
        self,
        rest_per_min: int = 30,
        ws_msg_per_min: int = 120,
        ws_max_conn_per_ip: int = 3,
        ws_relay_per_min: int = 1200,
    ) -> None:
        self.rest_per_min = rest_per_min
        self.ws_msg_per_min = ws_msg_per_min
        self.ws_max_conn_per_ip = ws_max_conn_per_ip
        self.ws_relay_per_min = ws_relay_per_min
        self._rest: dict = {}
        self._msg: dict = {}
        self._conn: dict = {}
        self._relay: dict = {}

    @staticmethod
    def _allow(bucket: dict, key: str, limit: int, window: float = 60.0) -> bool:
        now = time.monotonic()
        hits = [t for t in bucket.get(key, []) if now - t < window]
        if len(hits) >= limit:
            bucket[key] = hits
            return False
        hits.append(now)
        bucket[key] = hits
        return True

    def check_rest(self, ip: str) -> bool:
        return self._allow(self._rest, ip, self.rest_per_min)

    def check_ws_message(self, ip: str) -> bool:
        return self._allow(self._msg, ip, self.ws_msg_per_min)

    def check_ws_relay(self, ip: str) -> bool:
        """Remote-desktop frames (pairing, screen video, input): streamed
        traffic with pairing that must never fail silently, so they get a
        generous separate quota — the chat cap (2 msg/s) would otherwise
        throttle the screen to a slideshow."""
        return self._allow(self._relay, ip, self.ws_relay_per_min)

    def check_ws_connect(self, ip: str) -> bool:
        active = self._conn.get(ip, 0)
        if active >= self.ws_max_conn_per_ip:
            return False
        self._conn[ip] = active + 1
        return True

    def release_ws(self, ip: str) -> None:
        self._conn[ip] = max(0, self._conn.get(ip, 0) - 1)
