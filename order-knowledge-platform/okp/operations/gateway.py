"""Passerelle vers le système faisant autorité (ERP SIMULÉ : base SQLite locale).

Dans un déploiement réel, cette classe serait remplacée par un client de l'API ERP
(même interface). Le contrôle de concurrence optimiste est délégué à la source :
la mise à jour n'aboutit que si le statut ERP est toujours celui attendu.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class SourceUnavailable(Exception):
    pass


class SourceConflict(Exception):
    pass


class ErpGateway:
    def __init__(self, erp_path: Path):
        self.path = Path(erp_path)
        self.fail_next = False  # injection de panne pour les tests

    def transition(self, order_no: str, expected_code: str, new_code: str, fields: dict[str, str | None]) -> dict:
        if self.fail_next:
            self.fail_next = False
            raise SourceUnavailable("ERP injoignable (panne simulée)")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        allowed = {"CONFIRMED_TS", "SHIPPED_TS", "DELIVERED_TS", "CANCELLED_TS", "CANCEL_REASON", "TRACKING_NO",
                   "WAREHOUSE_CODE"}
        if set(fields) - allowed:
            raise ValueError(f"champs non autorisés : {set(fields) - allowed}")
        values = {k: (now if v == "@now" else v) for k, v in fields.items()}
        sets = ", ".join(f"{k} = ?" for k in values)
        conn = sqlite3.connect(self.path)
        try:
            cur = conn.execute(
                f"UPDATE ERP_ORDER SET STATUS_CD = ?, {sets + ', ' if sets else ''}UPDATED_AT = ?, "
                f"ROW_VERSION = ROW_VERSION + 1 WHERE ORD_NO = ? AND STATUS_CD = ?",
                [new_code, *values.values(), now, order_no, expected_code])
            if cur.rowcount != 1:
                row = conn.execute("SELECT STATUS_CD FROM ERP_ORDER WHERE ORD_NO=?", (order_no,)).fetchone()
                raise SourceConflict(f"statut ERP actuel {row[0] if row else 'absent'} ≠ attendu {expected_code}")
            conn.commit()
            version = conn.execute("SELECT ROW_VERSION FROM ERP_ORDER WHERE ORD_NO=?", (order_no,)).fetchone()[0]
        finally:
            conn.close()
        return {"system": "erp", "order_no": order_no, "status_code": new_code, "row_version": version, "at": now}
