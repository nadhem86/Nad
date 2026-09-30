"""Passerelles vers les systèmes sources, qui restent l'autorité pour leurs actions.

- SimulatedGateway : DÉMO. Elle enregistre l'écriture dans la table sim_source_write, qui est relue par
  les ingestions pour simuler l'état de la source. Ce n'est PAS une intégration réelle.
- VtigerLiveGateway : écrit réellement dans vTiger via le webservice (opération `revise`).
  NON TESTÉE contre une instance réelle dans ce dépôt.
Il n'existe pas de passerelle WMS réelle : aucun WMS n'est identifié dans le contexte.
"""

from __future__ import annotations

import json
from typing import Protocol

from .db import utcnow


class SourceUnavailable(Exception):
    pass


class Gateway(Protocol):
    system: str
    simulated: bool

    def write(self, module: str, record_id: str, patch: dict) -> dict: ...


class SimulatedGateway:
    simulated = True

    def __init__(self, conn, system: str):
        self.conn = conn
        self.system = system
        self.fail_next = False  # pour tester la désynchronisation / l'indisponibilité

    def write(self, module: str, record_id: str, patch: dict) -> dict:
        if self.fail_next:
            self.fail_next = False
            raise SourceUnavailable(f"{self.system} indisponible (simulation)")
        ts = utcnow()
        cur = self.conn.execute(
            "INSERT INTO sim_source_write (system, module, record_id, patch, ts) VALUES (?,?,?,?,?)",
            (self.system, module, record_id, json.dumps(patch, ensure_ascii=False), ts))
        return {"system": self.system, "simulated": True, "write_id": cur.lastrowid, "acknowledged_at": ts}


class VtigerLiveGateway:
    system = "vtiger"
    simulated = False

    def __init__(self, client):
        self.client = client  # hub.ingest.vtiger.VtigerWebserviceClient déjà authentifié

    def write(self, module: str, record_id: str, patch: dict) -> dict:
        try:
            res = self.client.update({"id": record_id, **patch})
        except Exception as e:  # réseau, droits vTiger, validation vTiger...
            raise SourceUnavailable(f"vTiger : {e}") from e
        return {"system": "vtiger", "simulated": False, "acknowledged_at": res.get("modifiedtime"), "record": res.get("id")}
