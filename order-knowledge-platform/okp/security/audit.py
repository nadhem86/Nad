"""Journal d'audit append-only chaîné par HMAC (clé secrète OKP_AUDIT_HMAC_KEY).

Chaque entrée contient le hash de la précédente ; toute modification, suppression ou
insertion au milieu de la chaîne est détectée par `verify()`. Les triggers SQL empêchent
UPDATE/DELETE ; le chaînage détecte une altération faite en contournant ces triggers.
Minimisation : les détails ne contiennent jamais de valeurs de champs personnels.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3

from okp.db import utcnow
from okp.security.policy import Principal

GENESIS = "0" * 64


class AuditLog:
    def __init__(self, conn: sqlite3.Connection, key: bytes, model_versions: dict[str, str]):
        self.conn = conn
        self.key = key
        self.versions = json.dumps(model_versions, sort_keys=True)

    def _digest(self, prev: str, row: dict) -> str:
        payload = json.dumps(row, sort_keys=True, ensure_ascii=False)
        return hmac.new(self.key, (prev + payload).encode(), hashlib.sha256).hexdigest()

    def record(self, principal: Principal | None, event_type: str, resource: str | None, decision: str,
               details: dict | None = None) -> int:
        last = self.conn.execute("SELECT hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
        prev = last["hash"] if last else GENESIS
        row = {
            "ts": utcnow(),
            "principal_id": principal.id if principal else "anonymous",
            "channel": principal.channel if principal else "unknown",
            "event_type": event_type,
            "resource": resource,
            "decision": decision,
            "details": json.dumps(details or {}, sort_keys=True, ensure_ascii=False, default=str),
            "model_versions": self.versions,
        }
        h = self._digest(prev, row)
        cur = self.conn.execute(
            "INSERT INTO audit_log (ts, principal_id, channel, event_type, resource, decision, details,"
            " model_versions, prev_hash, hash) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (row["ts"], row["principal_id"], row["channel"], row["event_type"], row["resource"], row["decision"],
             row["details"], row["model_versions"], prev, h))
        return cur.lastrowid

    def verify(self) -> dict:
        prev = GENESIS
        n = 0
        for r in self.conn.execute("SELECT * FROM audit_log ORDER BY seq"):
            row = {k: r[k] for k in ("ts", "principal_id", "channel", "event_type", "resource", "decision",
                                     "details", "model_versions")}
            if r["prev_hash"] != prev or self._digest(prev, row) != r["hash"]:
                return {"valid": False, "first_invalid_seq": r["seq"], "checked": n}
            prev = r["hash"]
            n += 1
        return {"valid": True, "first_invalid_seq": None, "checked": n}

    def events(self, limit: int = 100, event_type: str | None = None) -> list[dict]:
        sql = "SELECT seq, ts, principal_id, channel, event_type, resource, decision, details FROM audit_log"
        args: list = []
        if event_type:
            sql += " WHERE event_type = ?"
            args.append(event_type)
        sql += " ORDER BY seq DESC LIMIT ?"
        args.append(limit)
        return [{**dict(r), "details": json.loads(r["details"])} for r in self.conn.execute(sql, args)]
