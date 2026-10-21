"""Append-only JSONL log of every filing operation, kept in <root>/.sorter/manifest.jsonl.

A record for a move:
  {"id","ts","op":"move","item_id","md5","orig_name","orig_parent","new_name","new_parent",
   "category","confidence","method","decided_by","top"}
A record for an undo:
  {"id","ts","op":"undo","ref":<id of the move>,"item_id"}
"""
from __future__ import annotations

import json
import secrets
import time
from contextlib import contextmanager
from datetime import datetime, timezone

MANIFEST_NAME = "manifest.jsonl"
FLUSH_SECONDS = 2.0     # inside batch(): write at most this often (Drive rewrites the whole file on every write)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Manifest:
    def __init__(self, storage):
        self.storage = storage
        self.records: list[dict] = []
        self._pending: list[dict] = []
        self._batch, self._last_flush = 0, 0.0
        raw = storage.read_meta(MANIFEST_NAME) or b""
        self._torn = bool(raw) and not raw.endswith(b"\n")    # a crash cut the last line short
        for line in raw.decode("utf-8").splitlines():
            try:
                self.records.append(json.loads(line))
            except ValueError:
                continue

    # -- writing -----------------------------------------------------------
    def append(self, record: dict) -> dict:
        record = {"id": secrets.token_hex(4), "ts": now_iso(), **record}
        self.records.append(record)
        self._pending.append(record)
        if not self._batch or time.monotonic() - self._last_flush >= FLUSH_SECONDS:
            self.flush()
        return record

    @contextmanager
    def batch(self):
        """Group the appends of a bulk action into a few writes instead of one per move."""
        if not self._batch:
            self._last_flush = time.monotonic()
        self._batch += 1
        try:
            yield self
        finally:
            self._batch -= 1
            if not self._batch:
                self.flush()

    def flush(self) -> None:
        """Write the new records only (append, not rewrite)."""
        if not self._pending:
            return
        data = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in self._pending)
        if self._torn:
            data, self._torn = "\n" + data, False
        self.storage.append_meta(MANIFEST_NAME, data.encode("utf-8"))
        self._pending.clear()
        self._last_flush = time.monotonic()

    # -- queries -----------------------------------------------------------
    def moves(self) -> list[dict]:
        return [r for r in self.records if r.get("op") == "move"]

    def undone(self) -> set[str]:
        """ids (or, for old records, timestamps) of moves that were undone."""
        return {r.get("ref") or r.get("ref_ts") for r in self.records if r.get("op") == "undo"}

    def active_moves(self) -> list[dict]:
        """Moves that have not been undone and were not followed by a later move of the same file, oldest first.
        (Re-filing inbox -> X -> Y leaves only X -> Y, so undo walks back one step at a time.)"""
        undone = self.undone()
        out, moved_on = [], set()      # moved_on: files that a later move took further
        for r in reversed(self.moves()):
            if r.get("id", r["ts"]) in undone or r["ts"] in undone:
                continue
            if (r.get("new_id") or r["item_id"]) not in moved_on:
                out.append(r)
            moved_on.add(r["item_id"])
        out.reverse()
        return out

    def last_moves(self, n: int) -> list[dict]:
        return list(reversed(self.active_moves()))[:n]

    def filed_categories(self) -> dict[str, str]:
        """md5 -> category for every active user-decided or auto move (latest wins)."""
        out: dict[str, str] = {}
        for r in self.active_moves():
            if r.get("md5") and r.get("category"):
                out[r["md5"]] = r["category"]
        return out
