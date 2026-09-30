"""Règles d'inférence déterministes (voir `inferred_relations` du glossaire).

Les faits inférés sont stockés à part des faits importés : method='inference',
source_system='okp', rule_id et derived_from (liste des fact_id sources). Le recalcul est
idempotent : un fait inféré inchangé est conservé, un fait qui n'est plus dérivable est
rétracté (historique conservé), un fait dont les preuves changent est remplacé.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict

from okp.registry import Glossary

_CURRENT = "superseded_at IS NULL AND retracted_at IS NULL"

RULES = {
    "R-PURCHASE": (
        "purchased",
        f"""
        SELECT pb.object_id AS subject, rp.object_id AS object,
               pb.fact_id AS f1, hl.fact_id AS f2, rp.fact_id AS f3, st.fact_id AS f4
        FROM fact pb
        JOIN fact st ON st.subject = pb.subject AND st.predicate = 'order.status' AND st.{_CURRENT.replace(' AND ', ' AND st.')}
        JOIN fact hl ON hl.subject = pb.subject AND hl.predicate = 'hasLine' AND hl.{_CURRENT.replace(' AND ', ' AND hl.')}
        JOIN fact rp ON rp.subject = hl.object_id AND rp.predicate = 'refersToProduct' AND rp.{_CURRENT.replace(' AND ', ' AND rp.')}
        WHERE pb.predicate = 'placedBy' AND pb.{_CURRENT.replace(' AND ', ' AND pb.')}
          AND st.object_value NOT IN ('"draft"', '"cancelled"')
        """,
    ),
    "R-SUPPLY-DEP": (
        "dependsOnSupplier",
        f"""
        SELECT hl.subject AS subject, sb.object_id AS object,
               hl.fact_id AS f1, rp.fact_id AS f2, sb.fact_id AS f3, NULL AS f4
        FROM fact hl
        JOIN fact rp ON rp.subject = hl.object_id AND rp.predicate = 'refersToProduct' AND rp.{_CURRENT.replace(' AND ', ' AND rp.')}
        JOIN fact sb ON sb.subject = rp.object_id AND sb.predicate = 'suppliedBy' AND sb.{_CURRENT.replace(' AND ', ' AND sb.')}
        WHERE hl.predicate = 'hasLine' AND hl.{_CURRENT.replace(' AND ', ' AND hl.')}
        """,
    ),
}


def recompute(conn: sqlite3.Connection, glossary: Glossary, run_id: str, now: str) -> dict[str, int]:
    stats = {"added": 0, "retracted": 0, "unchanged": 0}
    declared = {r.rule_id: name for name, r in glossary.inferred_relations.items()}
    for rule_id, (predicate, sql) in RULES.items():
        assert declared.get(rule_id) == predicate, f"règle {rule_id} non déclarée dans le glossaire"
        evidence: dict[tuple[str, str], set[int]] = defaultdict(set)
        for row in conn.execute(sql):
            evidence[(row["subject"], row["object"])].update(f for f in (row["f1"], row["f2"], row["f3"], row["f4"]) if f)
        desired = {(s, o, json.dumps(sorted(ev))) for (s, o), ev in evidence.items()}
        existing = {}
        for f in conn.execute(
                f"SELECT fact_id, subject, object_id, derived_from FROM fact WHERE method='inference' AND rule_id=? "
                f"AND source_system='okp' AND {_CURRENT}", (rule_id,)):
            existing[(f["subject"], f["object_id"], f["derived_from"])] = f["fact_id"]
        for key, fid in existing.items():
            if key not in desired:
                conn.execute("UPDATE fact SET retracted_at=?, valid_to=? WHERE fact_id=?", (now, now, fid))
                stats["retracted"] += 1
            else:
                stats["unchanged"] += 1
        for s, o, derived in desired - set(existing):
            conn.execute(
                "INSERT INTO fact (subject, predicate, object_id, object_value, qualifiers, source_system, source_entity,"
                " source_id, method, rule_id, derived_from, validation_status, collected_at, valid_from, run_id)"
                " VALUES (?,?,?,NULL,'{}','okp','inference',?,'inference',?,?,'validated',?,?,?)",
                (s, predicate, o, rule_id, rule_id, derived, now, now, run_id))
            stats["added"] += 1
    return stats
