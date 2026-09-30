"""Requêtes sur le knowledge graph (faits courants ou historiques). Sans contrôle d'accès :
le filtrage par politique est appliqué par okp/services.py, seul point d'entrée public."""

from __future__ import annotations

import json
import sqlite3
from collections import deque
from typing import Any

CURRENT = "f.superseded_at IS NULL AND f.retracted_at IS NULL"


def provenance(f: sqlite3.Row) -> dict[str, Any]:
    p = {
        "fact_id": f["fact_id"],
        "kind": "inferred" if f["method"] == "inference" else "observed",
        "method": f["method"],
        "source_system": f["source_system"],
        "source_entity": f["source_entity"],
        "source_id": f["source_id"],
        "collected_at": f["collected_at"],
        "valid_from": f["valid_from"],
        "valid_to": f["valid_to"],
        "validation_status": f["validation_status"],
    }
    if f["method"] == "inference":
        p["rule_id"] = f["rule_id"]
        p["derived_from"] = json.loads(f["derived_from"])
    if f["superseded_at"] or f["retracted_at"]:
        p["ended"] = "superseded" if f["superseded_at"] else "retracted"
        p["ended_at"] = f["superseded_at"] or f["retracted_at"]
    return p


def entity(conn: sqlite3.Connection, cid: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM entity WHERE canonical_id=? AND deleted_at IS NULL", (cid,)).fetchone()


def facts_about(conn: sqlite3.Connection, cid: str, include_history: bool = False) -> list[sqlite3.Row]:
    where = "f.subject = ?" + ("" if include_history else f" AND {CURRENT}")
    return conn.execute(f"SELECT * FROM fact f WHERE {where} ORDER BY f.predicate, f.fact_id", (cid,)).fetchall()


def edges(conn: sqlite3.Connection, cid: str, include_inferred: bool = True) -> list[sqlite3.Row]:
    """Relations courantes sortantes et entrantes d'une entité."""
    extra = "" if include_inferred else " AND f.method <> 'inference'"
    return conn.execute(
        f"SELECT f.*, CASE WHEN f.subject = ? THEN 'out' ELSE 'in' END AS direction FROM fact f "
        f"WHERE (f.subject = ? OR f.object_id = ?) AND f.object_id IS NOT NULL AND {CURRENT}{extra} "
        f"ORDER BY f.predicate, f.fact_id", (cid, cid, cid)).fetchall()


def neighborhood(conn: sqlite3.Connection, cid: str, depth: int, include_inferred: bool,
                 predicates: set[str] | None = None, direction: str = "both") -> tuple[list[str], list[sqlite3.Row]]:
    """Parcours en largeur jusqu'à `depth` sauts. direction : both | out (dépendances) | in (impacts)."""
    seen, order, out_edges = {cid}, [cid], {}
    queue = deque([(cid, 0)])
    while queue:
        node, d = queue.popleft()
        if d >= depth:
            continue
        for e in edges(conn, node, include_inferred):
            if predicates and e["predicate"] not in predicates:
                continue
            if direction != "both" and e["direction"] != direction:
                continue
            out_edges[e["fact_id"]] = e
            other = e["object_id"] if e["direction"] == "out" else e["subject"]
            if other not in seen and entity(conn, other) is not None:
                seen.add(other)
                order.append(other)
                queue.append((other, d + 1))
    return order, list(out_edges.values())


def shortest_path(conn: sqlite3.Connection, a: str, b: str, max_depth: int = 4) -> list[sqlite3.Row] | None:
    prev: dict[str, tuple[str, sqlite3.Row] | None] = {a: None}
    queue = deque([(a, 0)])
    while queue:
        node, d = queue.popleft()
        if node == b:
            path = []
            while prev[node] is not None:
                node, e = prev[node][0], prev[node][1]
                path.append(e)
            return list(reversed(path))
        if d >= max_depth:
            continue
        for e in edges(conn, node, include_inferred=False):
            other = e["object_id"] if e["direction"] == "out" else e["subject"]
            if other not in prev:
                prev[other] = (node, e)
                queue.append((other, d + 1))
    return None


def resolve(conn: sqlite3.Connection, query: str, concept: str | None = None) -> list[dict[str, Any]]:
    """Identifiant canonique, identifiant source (tout système), puis libellé (contient)."""
    q = query.strip()
    hits: dict[str, dict] = {}

    def add(cid: str, how: str):
        e = entity(conn, cid)
        if e is not None and (concept is None or e["concept"] == concept) and cid not in hits:
            hits[cid] = {"canonical_id": cid, "concept": e["concept"], "label": e["label"], "matched_by": how}

    add(q.upper(), "canonical_id")
    if hits:
        return list(hits.values())
    for r in conn.execute("SELECT canonical_id, source_system, source_entity FROM id_map WHERE source_id = ? COLLATE NOCASE", (q,)):
        add(r["canonical_id"], f"source_id:{r['source_system']}/{r['source_entity']}")
    if hits:
        return list(hits.values())
    for r in conn.execute("SELECT canonical_id FROM entity WHERE deleted_at IS NULL AND label LIKE ? ESCAPE '\\' "
                          "ORDER BY label LIMIT 20", (f"%{_escape_like(q)}%",)):
        add(r["canonical_id"], "label")
    return list(hits.values())


def source_ids(conn: sqlite3.Connection, cid: str) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        "SELECT source_system, source_entity, source_id, match_rule, confidence FROM id_map WHERE canonical_id=? "
        "ORDER BY source_system", (cid,))]


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
