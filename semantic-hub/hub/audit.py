"""Journal d'audit chaîné : chaque entrée contient le hash de la précédente (altération détectable)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from .db import utcnow

GENESIS = "0" * 64


class AuditLog:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def record(self, *, actor: str, roles: list[str] | None, kind: str, action: str, target: str | None,
               request: Any, outcome: str, detail: Any = None) -> int:
        prev = self.conn.execute("SELECT hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
        prev_hash = prev["hash"] if prev else GENESIS
        row = {"ts": utcnow(), "actor": actor, "roles": json.dumps(roles or []), "kind": kind, "action": action,
               "target": target, "request": json.dumps(request, ensure_ascii=False, default=str, sort_keys=True),
               "outcome": outcome, "detail": json.dumps(detail, ensure_ascii=False, default=str, sort_keys=True)}
        h = _hash(prev_hash, row)
        cur = self.conn.execute(
            "INSERT INTO audit_log (ts, actor, roles, kind, action, target, request, outcome, detail, prev_hash, hash) "
            "VALUES (:ts,:actor,:roles,:kind,:action,:target,:request,:outcome,:detail,:prev,:hash)",
            {**row, "prev": prev_hash, "hash": h})
        return cur.lastrowid

    def verify(self) -> tuple[bool, int | None]:
        prev = GENESIS
        for r in self.conn.execute("SELECT * FROM audit_log ORDER BY seq"):
            row = {k: r[k] for k in ("ts", "actor", "roles", "kind", "action", "target", "request", "outcome", "detail")}
            if r["prev_hash"] != prev or _hash(prev, row) != r["hash"]:
                return False, r["seq"]
            prev = r["hash"]
        return True, None

    def tail(self, n: int = 50, kind: str | None = None) -> list[dict]:
        q = "SELECT seq, ts, actor, kind, action, target, outcome, detail FROM audit_log"
        args: list = []
        if kind:
            q += " WHERE kind=?"; args.append(kind)
        return [dict(r) for r in self.conn.execute(q + " ORDER BY seq DESC LIMIT ?", (*args, n))]


def _hash(prev: str, row: dict) -> str:
    return hashlib.sha256((prev + json.dumps(row, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()
