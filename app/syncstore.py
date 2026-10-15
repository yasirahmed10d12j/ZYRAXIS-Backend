"""Shared hub stores: cross-device memory + tasks with JSON persistence.

These are the ecosystem substrate: every ZYRAXIS client (iOS, Android,
Desktop) reads/writes the SAME memory and task list through the REST API
below, so "remember on laptop, recall on phone" actually works.

Design notes (honest limits):
- Single laptop server, single event loop: plain dicts + atomic file
  replace (os.replace) are enough. No DB, no migrations.
-Substring search + importance ordering mirror the iOS InMemory services,
  so behavior matches what clients already expect locally.
- Reminders NOTIFY only on the device that created them (local
  notifications are per-device by OS design). The server holds the shared
  list; firing stays client-side until push infrastructure exists.
"""
from __future__ import annotations

import json
import os
import time
import uuid


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


class JsonStore:
    """Atomic JSON-file dict store: {id: item}. Crashes never leave halves."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.items: dict = {}
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self.items = data
        except (OSError, ValueError):
            self.items = {}

    def persist(self) -> None:
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.items, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)


class MemoryStore(JsonStore):
    """Shared memory items: {id, content, tags, importance, createdAt, updatedAt}."""

    def save(self, content: str, tags=None, importance: float = 0.5) -> dict:
        text = (content or "").strip()
        if not text:
            raise ValueError("empty content")
        item = {
            "id": uuid.uuid4().hex,
            "content": text[:2000],
            "tags": [str(t)[:40] for t in (tags or [])][:8],
            "importance": max(0.0, min(1.0, float(importance))),
            "createdAt": _now_iso(),
            "updatedAt": _now_iso(),
        }
        self.items[item["id"]] = item
        self.persist()
        return item

    def search(self, query: str = "", limit: int = 20) -> list:
        q = (query or "").lower().strip()
        all_items = list(self.items.values())
        if q:
            all_items = [m for m in all_items
                         if q in str(m.get("content", "")).lower()
                         or any(q in str(t).lower() for t in m.get("tags", []))]
        all_items.sort(key=lambda m: (m.get("importance", 0), m.get("updatedAt", "")),
                       reverse=True)
        return all_items[:max(1, min(100, limit))]

    def forget(self, item_id: str) -> bool:
        if item_id in self.items:
            del self.items[item_id]
            self.persist()
            return True
        return False


class TaskStore(JsonStore):
    """Shared tasks: {id, title, notes, dueDate, priority, isCompleted,
    isCancelled, createdAt}. dueDate is ISO local time or null."""

    VALID = ("low", "medium", "high")

    def create(self, title: str, notes=None, due_date=None, priority: str = "medium") -> dict:
        title = (title or "").strip()
        if not title:
            raise ValueError("empty title")
        if priority not in self.VALID:
            priority = "medium"
        item = {
            "id": uuid.uuid4().hex,
            "title": title[:300],
            "notes": (str(notes or "")[:1000] or None),
            "dueDate": due_date,
            "priority": priority,
            "isCompleted": False,
            "isCancelled": False,
            "createdAt": _now_iso(),
        }
        self.items[item["id"]] = item
        self.persist()
        return item

    def list(self, filt: str = "active") -> list:
        items = list(self.items.values())
        if filt == "active":
            items = [t for t in items if not t.get("isCompleted") and not t.get("isCancelled")]
        elif filt == "completed":
            items = [t for t in items if t.get("isCompleted")]
        elif filt == "cancelled":
            items = [t for t in items if t.get("isCancelled")]
        # "all" (or anything else): everything.
        items.sort(key=lambda t: t.get("createdAt", ""), reverse=True)
        return items

    def _mutate(self, task_id: str, **fields) -> dict | None:
        item = self.items.get(task_id)
        if item is None:
            return None
        item.update(fields)
        self.persist()
        return item

    def complete(self, task_id: str) -> dict | None:
        return self._mutate(task_id, isCompleted=True)

    def cancel(self, task_id: str) -> dict | None:
        return self._mutate(task_id, isCancelled=True)

    def snooze(self, task_id: str, until: str) -> dict | None:
        return self._mutate(task_id, dueDate=until)
