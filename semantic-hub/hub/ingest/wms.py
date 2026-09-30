"""Ingestion WMS (SIMULÉ par des CSV : aucun WMS réel n'existe dans le contexte).

Le WMS est l'autorité pour : Entrepot, affectation/préparation/expédition/livraison, retours et stock.
Les commandes sont résolues par clé métier (numeroCommande = salesorder_no vTiger) : c'est de la
résolution d'identité inter-sources. Une référence non résolue est rejetée et signalée, jamais inventée.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from ..db import utcnow
from ..kg import IRI, KnowledgeGraph

SOURCE = "wms"
STAGE_ORDER = {"affectation": 1, "expedition": 2, "livraison": 3}
STAGE_TO_STATUT = {"affectation": "en_preparation", "expedition": "expediee", "livraison": "livree"}


class WmsIngestor:
    def __init__(self, kg: KnowledgeGraph, run_id: str):
        self.kg = kg
        self.run_id = run_id
        self.stats: Counter = Counter()
        self.rejected: list[dict] = []

    def ingest_dir(self, directory: Path, collected_at: str | None = None) -> dict:
        collected = collected_at or utcnow()
        self.ingest_warehouses(directory / "warehouses.csv", collected)
        events = list(_read_csv(directory / "events.csv"))
        events += self._simulated_events()
        self.ingest_events(events, collected)
        if (directory / "stock.csv").exists():
            self.ingest_stock(directory / "stock.csv", collected)
        return {"stats": dict(self.stats), "rejected": self.rejected}

    def _simulated_events(self) -> list[dict]:
        """Démo : événements écrits via la passerelle WMS SIMULÉE (actions affecter/expédier)."""
        rows = self.kg.conn.execute(
            "SELECT patch FROM sim_source_write WHERE system='wms' AND module='events' ORDER BY id").fetchall()
        if rows:
            self.stats["evenements_simules"] += len(rows)
        return [json.loads(r["patch"]) for r in rows]

    def ingest_warehouses(self, path: Path, collected: str) -> None:
        for r in _read_csv(path):
            iri = self.kg.ensure_entity("Entrepot", SOURCE, "warehouses", r["code"], label=r["nom"])
            s = self.kg.upsert_facts(iri, {"code": r["code"], "nom": r["nom"], "ville": r.get("ville") or None,
                                           "region": r["region"]},
                                     source=SOURCE, source_record_id=f"warehouses/{r['code']}", collected_at=collected,
                                     valid_from=None, run_id=self.run_id)
            self._count(s)

    def ingest_events(self, events: list[dict], collected: str) -> None:
        by_order: dict[str, list[dict]] = defaultdict(list)
        for e in events:
            by_order[e["salesorder_no"]].append(e)
        for so_no, evs in by_order.items():
            matches = self.kg.find_by_value("Commande", "numeroCommande", so_no)
            if len(matches) != 1:
                self.rejected.append({"salesorder_no": so_no, "reason": f"{len(matches)} commande(s) correspondante(s)"})
                self.stats["rejected_events"] += len(evs)
                continue
            order = matches[0]
            self.kg.link_identity(order, SOURCE, "salesorder_no", so_no)
            evs.sort(key=lambda e: e["ts_utc"])
            values: dict = {}
            stage = None
            returns: dict[int, float] = defaultdict(float)
            for e in evs:
                kind = e["event"]
                if kind in STAGE_ORDER:
                    if stage is None or STAGE_ORDER[kind] >= STAGE_ORDER[stage]:
                        stage = kind
                if kind == "affectation":
                    wh = self.kg.resolve_source(SOURCE, "warehouses", e["warehouse_code"])
                    if not wh:
                        self.rejected.append({"event_id": e["event_id"], "reason": f"entrepôt inconnu {e['warehouse_code']}"})
                        continue
                    values.update(prepareeDans=IRI(wh), dateAffectation=e["ts_utc"])
                elif kind == "expedition":
                    values.update(dateExpedition=e["ts_utc"], transporteur=e.get("carrier") or None,
                                  numeroSuivi=e.get("tracking") or None)
                elif kind == "livraison":
                    values["dateLivraison"] = e["ts_utc"]
                elif kind == "retour":
                    returns[int(e["line_sequence"])] += float(e["quantity"])
                self.stats["events"] += 1
            if stage:
                values["statutLogistique"] = STAGE_TO_STATUT[stage]
            s = self.kg.upsert_facts(order, values, source=SOURCE, source_record_id=f"events/{so_no}",
                                     collected_at=collected, valid_from=evs[-1]["ts_utc"], run_id=self.run_id)
            self._count(s)
            for seq, qty in returns.items():
                lines = [l for l, _ in self.kg.traverse(order, ["^ligneDe"]) if self.kg.get(l, "numeroLigne") == seq]
                if len(lines) != 1:
                    self.rejected.append({"salesorder_no": so_no, "line": seq, "reason": "ligne introuvable pour retour"})
                    continue
                s = self.kg.upsert_facts(lines[0], {"quantiteRetournee": qty}, source=SOURCE,
                                         source_record_id=f"events/{so_no}/retour/{seq}", collected_at=collected,
                                         valid_from=None, run_id=self.run_id)
                self._count(s)

    def ingest_stock(self, path: Path, collected: str) -> None:
        for r in _read_csv(path):
            prods = self.kg.find_by_value("Produit", "reference", r["product_ref"])
            wh = self.kg.resolve_source(SOURCE, "warehouses", r["warehouse_code"])
            if len(prods) != 1 or not wh:
                self.rejected.append({"stock": r, "reason": "produit ou entrepôt non résolu"})
                continue
            key = f"{r['date']}|{r['warehouse_code']}|{r['product_ref']}"
            iri = self.kg.ensure_entity("ReleveStock", SOURCE, "stock", key, label=key)
            s = self.kg.upsert_facts(iri, {"dateReleve": r["date"], "quantite": r["quantity"],
                                           "stockDe": IRI(prods[0]), "stockDans": IRI(wh)},
                                     source=SOURCE, source_record_id=f"stock/{key}", collected_at=collected,
                                     valid_from=r["date"] + "T00:00:00Z", run_id=self.run_id)
            self._count(s)

    def _count(self, s: Counter) -> None:
        self.stats.update({f"facts_{k}": v for k, v in s.items()})


def _read_csv(path: Path):
    with open(path, newline="", encoding="utf-8") as f:
        yield from csv.DictReader(f)
