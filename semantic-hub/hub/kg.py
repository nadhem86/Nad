"""Knowledge graph : entités, identités sources et faits bitemporels avec provenance.

Le stockage est relationnel (SQLite). Chaque fait est un triplet (sujet, prédicat, objet) accompagné
de sa provenance : source, identifiant source, méthode, règle, prémisses, statut, validité métier et
temps d'enregistrement. Un fait n'est jamais modifié : il est remplacé (superseded_at renseigné).
C'est ce qui garantit l'historique, l'idempotence et la traçabilité.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from .db import utcnow
from .glossary import Glossary, Relation
from .ids import canonical_iri

ACTIVE_STATUSES = ("valide", "a_verifier")


@dataclass(frozen=True)
class IRI:
    """Marqueur explicite d'une valeur IRI (sinon la valeur est un littéral)."""

    value: str


def serialize(value: Any, datatype: str) -> str:
    if datatype == "boolean":
        if isinstance(value, str):
            return "true" if value.strip().lower() in ("1", "true", "oui", "yes") else "false"
        return "true" if value else "false"
    if datatype in ("decimal", "integer"):
        try:
            d = Decimal(str(value))
        except InvalidOperation as e:
            raise ValueError(f"valeur numérique invalide : {value!r}") from e
        if datatype == "integer":
            return str(int(d))
        s = format(d.normalize(), "f")
        return s if s not in ("-0",) else "0"
    if datatype == "dateTime":
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%dT%H:%M:%SZ")
        return str(value)
    if datatype == "date" and isinstance(value, date):
        return value.isoformat()
    return str(value)


def deserialize(value: str | None, datatype: str | None) -> Any:
    if value is None:
        return None
    if datatype == "decimal":
        return float(value)
    if datatype == "integer":
        return int(value)
    if datatype == "boolean":
        return value == "true"
    return value


class KnowledgeGraph:
    def __init__(self, conn: sqlite3.Connection, glossary: Glossary):
        self.conn = conn
        self.g = glossary

    # ------------------------------------------------------------------ entités et identités
    def ensure_entity(self, concept: str, source: str, source_type: str, source_id: str,
                      label: str | None = None, at: str | None = None) -> str:
        """Résout (ou crée) l'IRI canonique correspondant à un identifiant source."""
        now = at or utcnow()
        row = self.conn.execute(
            "SELECT iri FROM source_identity WHERE source=? AND source_type=? AND source_id=?",
            (source, source_type, str(source_id)),
        ).fetchone()
        if row:
            iri = row["iri"]
            self.conn.execute(
                "UPDATE source_identity SET last_seen=? WHERE source=? AND source_type=? AND source_id=?",
                (now, source, source_type, str(source_id)),
            )
            ent = self.conn.execute("SELECT concept, deleted_at FROM entity WHERE iri=?", (iri,)).fetchone()
            if ent["concept"] != concept:
                raise ValueError(f"{source}/{source_type}/{source_id} est déjà un {ent['concept']}, pas un {concept}")
            if ent["deleted_at"]:  # réapparition dans la source : l'entité redevient active
                self.conn.execute("UPDATE entity SET deleted_at=NULL, deleted_reason=NULL WHERE iri=?", (iri,))
            if label:
                self.conn.execute("UPDATE entity SET label=? WHERE iri=?", (label, iri))
            return iri
        slug = self.g.concept(concept).slug
        iri = canonical_iri(slug, source, source_type, str(source_id))
        self.conn.execute(
            "INSERT OR IGNORE INTO entity (iri, concept, label, created_at) VALUES (?,?,?,?)",
            (iri, concept, label, now),
        )
        self.link_identity(iri, source, source_type, source_id, at=now)
        return iri

    def link_identity(self, iri: str, source: str, source_type: str, source_id: str, at: str | None = None) -> None:
        now = at or utcnow()
        self.conn.execute(
            "INSERT INTO source_identity (source, source_type, source_id, iri, first_seen, last_seen) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(source, source_type, source_id) DO UPDATE SET last_seen=excluded.last_seen",
            (source, source_type, str(source_id), iri, now, now),
        )

    def resolve_source(self, source: str, source_type: str, source_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT iri FROM source_identity WHERE source=? AND source_type=? AND source_id=?",
            (source, source_type, str(source_id)),
        ).fetchone()
        return row["iri"] if row else None

    def source_ids(self, iri: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT source, source_type, source_id, first_seen, last_seen FROM source_identity WHERE iri=? ORDER BY source",
            (iri,))]

    def entity(self, iri: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM entity WHERE iri=?", (iri,)).fetchone()
        return dict(row) if row else None

    def concept_of(self, iri: str) -> str | None:
        row = self.conn.execute("SELECT concept FROM entity WHERE iri=?", (iri,)).fetchone()
        return row["concept"] if row else None

    def entities(self, concept: str, include_deleted: bool = False) -> list[str]:
        q = "SELECT iri FROM entity WHERE concept=?" + ("" if include_deleted else " AND deleted_at IS NULL")
        return [r["iri"] for r in self.conn.execute(q + " ORDER BY iri", (concept,))]

    def find_by_value(self, concept: str, predicate: str, value: Any) -> list[str]:
        member = self.g.member(concept, predicate)
        sval = serialize(value, member.type) if not member.is_relation else None
        rows = self.conn.execute(
            "SELECT DISTINCT f.subject FROM fact f JOIN entity e ON e.iri=f.subject "
            "WHERE e.concept=? AND e.deleted_at IS NULL AND f.predicate=? AND f.superseded_at IS NULL "
            "AND f.status IN ('valide','a_verifier') AND (f.object_value=? OR f.object_iri=?)",
            (concept, predicate, sval, value if member.is_relation else None),
        )
        return [r["subject"] for r in rows]

    # ------------------------------------------------------------------ écriture de faits sourcés
    def upsert_facts(self, subject: str, values: dict[str, Any], *, source: str, source_record_id: str | None,
                     collected_at: str, valid_from: str | None, run_id: str | None, method: str = "import",
                     actor: str | None = None, replace_missing: bool = True, status: str = "valide") -> Counter:
        """Réconcilie les faits d'UNE source pour un sujet avec les valeurs observées.

        - valeur identique : rien n'est écrit (idempotence) ;
        - valeur changée : l'ancien fait est remplacé (valid_to = valid_from du nouveau) ;
        - prédicat absent et replace_missing : l'ancien fait est retiré (valeur supprimée dans la source).
        Seuls les faits de la même source sont concernés : une source n'écrase jamais une autre source.
        """
        concept = self.concept_of(subject)
        if concept is None:
            raise KeyError(f"entité inconnue : {subject}")
        stats: Counter = Counter()
        now = utcnow()
        wanted: dict[str, set[tuple[str | None, str | None]]] = {}
        datatypes: dict[str, str] = {}
        for pred, val in values.items():
            member = self.g.member(concept, pred)  # KeyError si le prédicat n'existe pas dans le glossaire
            if getattr(member, "inferred_only", False) and method != "inference":
                raise ValueError(f"{concept}.{pred} ne peut être qu'inféré")
            vals = val if isinstance(val, (list, set, tuple)) else [val]
            items: set[tuple[str | None, str | None]] = set()
            for v in vals:
                if v is None or v == "":
                    continue
                if isinstance(member, Relation):
                    items.add((v.value if isinstance(v, IRI) else str(v), None))
                    datatypes[pred] = "iri"
                else:
                    items.add((None, serialize(v, member.type)))
                    datatypes[pred] = member.type
            wanted[pred] = items

        current = self.conn.execute(
            "SELECT fact_id, predicate, object_iri, object_value FROM fact "
            "WHERE subject=? AND source=? AND method<>'inference' AND superseded_at IS NULL",
            (subject, source),
        ).fetchall()
        by_pred: dict[str, dict[tuple, int]] = {}
        for r in current:
            by_pred.setdefault(r["predicate"], {})[(r["object_iri"], r["object_value"])] = r["fact_id"]

        preds = set(wanted) | (set(by_pred) if replace_missing else set())
        for pred in preds:
            old = by_pred.get(pred, {})
            new = wanted.get(pred, set()) if pred in wanted else set()
            if pred not in wanted and not replace_missing:
                continue
            for key, fid in old.items():
                if key not in new:
                    self.conn.execute(
                        "UPDATE fact SET superseded_at=?, superseded_reason=?, valid_to=COALESCE(?, ?) WHERE fact_id=?",
                        (now, "valeur_modifiee" if new else "valeur_retiree", valid_from, collected_at, fid),
                    )
                    stats["superseded"] += 1
            for key in new:
                if key in old:
                    stats["unchanged"] += 1
                    continue
                self.conn.execute(
                    "INSERT INTO fact (subject, predicate, object_iri, object_value, datatype, source, source_record_id, "
                    "method, status, collected_at, valid_from, recorded_at, run_id, actor) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (subject, pred, key[0], key[1], datatypes.get(pred), source, source_record_id, method, status,
                     collected_at, valid_from, now, run_id, actor),
                )
                stats["inserted"] += 1
        return stats

    def retract_entity(self, iri: str, *, source: str, at: str, reason: str) -> int:
        """Suppression constatée dans la source : tombstone + retrait des faits de cette source."""
        cur = self.conn.execute(
            "UPDATE fact SET superseded_at=?, superseded_reason=?, valid_to=COALESCE(valid_to, ?) "
            "WHERE subject=? AND source=? AND method<>'inference' AND superseded_at IS NULL",
            (utcnow(), "entite_supprimee_dans_source", at, iri, source),
        )
        self.conn.execute(
            "UPDATE entity SET deleted_at=?, deleted_reason=? WHERE iri=? AND deleted_at IS NULL", (at, reason, iri)
        )
        return cur.rowcount

    # ------------------------------------------------------------------ faits inférés
    def replace_inferred(self, rule_id: str, facts: Iterable[dict], run_id: str | None) -> Counter:
        """Remplace l'ensemble des faits produits par une règle (dérivation complète, idempotente)."""
        stats: Counter = Counter()
        now = utcnow()
        current = {}
        for r in self.conn.execute(
            "SELECT fact_id, subject, predicate, object_iri, object_value, status, derived_from FROM fact "
            "WHERE rule_id=? AND method='inference' AND superseded_at IS NULL", (rule_id,)):
            current[(r["subject"], r["predicate"], r["object_iri"], r["object_value"], r["status"], r["derived_from"])] = r["fact_id"]
        wanted = {}
        for f in facts:
            concept = self.concept_of(f["subject"])
            member = self.g.member(concept, f["predicate"])
            if isinstance(member, Relation):
                oi, ov, dt = f["object"], None, "iri"
            else:
                oi, ov, dt = None, serialize(f["object"], member.type), member.type
            key = (f["subject"], f["predicate"], oi, ov, f.get("status", "valide"),
                   json.dumps(sorted(set(f["derived_from"]))))
            wanted[key] = (dt, f.get("confidence", 1.0), f.get("valid_from"))
        for key, fid in current.items():
            if key not in wanted:
                self.conn.execute(
                    "UPDATE fact SET superseded_at=?, superseded_reason='premisses_modifiees', valid_to=? WHERE fact_id=?",
                    (now, now, fid))
                stats["superseded"] += 1
        for key, (dt, conf, vf) in wanted.items():
            if key in current:
                stats["unchanged"] += 1
                continue
            s, p, oi, ov, st, df = key
            self.conn.execute(
                "INSERT INTO fact (subject, predicate, object_iri, object_value, datatype, source, source_record_id, method, "
                "rule_id, derived_from, confidence, status, collected_at, valid_from, recorded_at, run_id) "
                "VALUES (?,?,?,?,?,'hub',NULL,'inference',?,?,?,?,?,?,?,?)",
                (s, p, oi, ov, dt, rule_id, df, conf, st, now, vf or now, now, run_id))
            stats["inserted"] += 1
        return stats

    # ------------------------------------------------------------------ lecture
    def facts(self, subject: str | None = None, predicate: str | None = None, *, obj: str | None = None,
              include_inferred: bool = True, statuses: tuple[str, ...] = ACTIVE_STATUSES) -> list[sqlite3.Row]:
        q = "SELECT * FROM fact WHERE superseded_at IS NULL"
        args: list[Any] = []
        if subject:
            q += " AND subject=?"; args.append(subject)
        if predicate:
            q += " AND predicate=?"; args.append(predicate)
        if obj:
            q += " AND object_iri=?"; args.append(obj)
        if not include_inferred:
            q += " AND method<>'inference'"
        q += f" AND status IN ({','.join('?' * len(statuses))}) ORDER BY fact_id"
        args += list(statuses)
        return self.conn.execute(q, args).fetchall()

    def get(self, subject: str, predicate: str, statuses: tuple[str, ...] = ("valide",)) -> Any:
        """Valeur courante d'un prédicat mono-valué (la plus récente si plusieurs sources)."""
        rows = self.conn.execute(
            f"SELECT object_iri, object_value, datatype FROM fact WHERE subject=? AND predicate=? AND superseded_at IS NULL "
            f"AND status IN ({','.join('?' * len(statuses))}) ORDER BY COALESCE(valid_from, recorded_at) DESC, fact_id DESC LIMIT 1",
            (subject, predicate, *statuses)).fetchone()
        if not rows:
            return None
        return rows["object_iri"] if rows["object_iri"] is not None else deserialize(rows["object_value"], rows["datatype"])

    def get_all(self, subject: str, predicate: str) -> list[Any]:
        return [r["object_iri"] if r["object_iri"] is not None else deserialize(r["object_value"], r["datatype"])
                for r in self.facts(subject, predicate, statuses=("valide",))]

    def fact_for(self, subject: str, predicate: str) -> sqlite3.Row | None:
        rows = self.conn.execute(
            "SELECT * FROM fact WHERE subject=? AND predicate=? AND superseded_at IS NULL AND status='valide' "
            "ORDER BY COALESCE(valid_from, recorded_at) DESC, fact_id DESC LIMIT 1", (subject, predicate)).fetchone()
        return rows

    def value_at(self, subject: str, predicate: str, at: str) -> tuple[Any, bool]:
        """Valeur valide à la date métier `at` (historique). Retourne (valeur, trouvée_historiquement)."""
        row = self.conn.execute(
            "SELECT object_value, datatype FROM fact WHERE subject=? AND predicate=? AND method<>'inference' "
            "AND status='valide' AND (valid_from IS NULL OR valid_from<=?) AND (valid_to IS NULL OR valid_to>?) "
            "ORDER BY valid_from DESC, fact_id DESC LIMIT 1", (subject, predicate, at, at)).fetchone()
        if row:
            return deserialize(row["object_value"], row["datatype"]), True
        return self.get(subject, predicate), False

    def traverse(self, start: str, path: list[str]) -> list[tuple[str, list[int]]]:
        """Parcourt un chemin de relations ('^rel' = sens inverse). Retourne (iri, fact_ids du chemin)."""
        frontier: list[tuple[str, list[int]]] = [(start, [])]
        for step in path:
            inverse = step.startswith("^")
            pred = step.lstrip("^")
            nxt: dict[str, list[int]] = {}
            for node, trail in frontier:
                col, other = ("object_iri", "subject") if inverse else ("subject", "object_iri")
                for r in self.conn.execute(
                    f"SELECT fact_id, {other} AS o FROM fact WHERE {col}=? AND predicate=? AND superseded_at IS NULL "
                    "AND status='valide' AND object_iri IS NOT NULL", (node, pred)):
                    nxt.setdefault(r["o"], trail + [r["fact_id"]])
            frontier = list(nxt.items())
        return frontier

    def fact_ids_exist(self, ids: Iterable[int]) -> bool:
        ids = list(ids)
        if not ids:
            return True
        n = self.conn.execute(f"SELECT COUNT(*) FROM fact WHERE fact_id IN ({','.join('?' * len(ids))})", ids).fetchone()[0]
        return n == len(set(ids))


def provenance(row: sqlite3.Row) -> dict:
    """Provenance d'un fait, telle qu'exposée aux API et aux agents."""
    d = {
        "fact_id": row["fact_id"],
        "source": row["source"],
        "source_record_id": row["source_record_id"],
        "method": row["method"],
        "status": row["status"],
        "collected_at": row["collected_at"],
        "valid_from": row["valid_from"],
        "valid_to": row["valid_to"],
    }
    if row["method"] == "inference":
        d.update(rule_id=row["rule_id"], derived_from=json.loads(row["derived_from"] or "[]"),
                 confidence=row["confidence"])
    if row["actor"]:
        d["actor"] = row["actor"]
    return d
