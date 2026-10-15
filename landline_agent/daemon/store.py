"""Persistence for tickets and the allowlist (small JSON files, 0600)."""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from pathlib import Path

from ..domain import approval as ap
from ..domain.number import normalize


def _write(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


class TicketStore:
    def __init__(self, path: Path):
        self.path = path
        self.tickets: dict[str, ap.Ticket] = {}
        if path.exists():
            for tid, d in json.loads(path.read_text(encoding="utf-8")).items():
                self.tickets[tid] = self._load(d)

    @staticmethod
    def _dump(t: ap.Ticket) -> dict:
        d = {"state": type(t).__name__, "data": asdict(t.data)}
        for k in ("decided_by", "use_until", "used_at"):
            if hasattr(t, k):
                d[k] = getattr(t, k)
        return d

    @staticmethod
    def _load(d: dict) -> ap.Ticket:
        data = ap.TicketData(**d["data"])
        return {
            "Pending": lambda: ap.Pending(data),
            "Approved": lambda: ap.Approved(data, d["decided_by"], d["use_until"]),
            "Denied": lambda: ap.Denied(data, d["decided_by"]),
            "Expired": lambda: ap.Expired(data),
            "Consumed": lambda: ap.Consumed(data, d["used_at"]),
        }[d["state"]]()

    def put(self, t: ap.Ticket) -> None:
        self.tickets[t.data.ticket_id] = t
        week = time.time() - 7 * 86400
        self.tickets = {k: v for k, v in self.tickets.items() if v.data.created > week}
        _write(self.path, {k: self._dump(v) for k, v in self.tickets.items()})

    def get(self, tid: str) -> ap.Ticket | None:
        return self.tickets.get(tid)


class AllowList:
    """Standing approvals.  Only the operator edits it (CLI); the API cannot."""

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def numbers(self) -> frozenset[str]:
        return frozenset(self.load())

    def add(self, number: str, label: str) -> str:
        d = self.load()
        n = normalize(number)
        d[n] = label
        _write(self.path, d)
        return n

    def remove(self, number: str) -> bool:
        d = self.load()
        if d.pop(normalize(number), None) is None:
            return False
        _write(self.path, d)
        return True
