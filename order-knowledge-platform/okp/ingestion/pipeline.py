"""Pipeline d'ingestion : sources → identités canoniques → core relationnel + knowledge graph.

Garanties :
  - Idempotence : un enregistrement dont l'empreinte n'a pas changé n'est pas retraité ;
    réimporter les mêmes sources ne crée aucun fait.
  - Traçabilité : chaque fait porte système, entité et identifiant source, date de collecte,
    période de validité métier, méthode et statut de validation.
  - Historique : un fait remplacé n'est pas effacé (superseded_at + valid_to).
  - Suppressions : disparition d'un enregistrement (mode complet) ou événement 'D' du CDC
    (mode incrémental) → faits rétractés, entité et ligne core marquées supprimées.
  - Atomicité : une exécution = une transaction.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from okp.db import utcnow
from okp.ingestion import inference
from okp.ingestion.sources import (
    PROCESSING_ORDER, ErpSource, FileSources, MappedRecord, Ref, SourceRecord, entity_label, map_record, normalize_name,
)
from okp.ingestion.validation import validate_props
from okp.registry import Registry

ID_NAMESPACE = uuid.UUID("6f1c3a52-6f0e-4d0b-9d5e-0a4b1c2d3e4f")


def mint_canonical_id(prefix: str, ref: Ref) -> str:
    """Identifiant canonique stable et reproductible, indépendant de l'ordre d'import."""
    u = uuid.uuid5(ID_NAMESPACE, f"{ref.system}|{ref.entity}|{ref.source_id}")
    return f"{prefix}-{u.hex[:10].upper()}"


@dataclass
class RunReport:
    run_id: str
    mode: str
    stats: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    issues: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"run_id": self.run_id, "mode": self.mode, "stats": dict(self.stats), "issues": self.issues}


class _Skip(Exception):
    pass


class IngestionPipeline:
    def __init__(self, conn: sqlite3.Connection, registry: Registry, sources_dir: Path):
        self.conn = conn
        self.reg = registry
        self.g = registry.glossary
        self.erp = ErpSource(Path(sources_dir) / "erp.sqlite")
        self.files = FileSources(Path(sources_dir))

    # ------------------------------------------------------------------ orchestration
    def run(self, mode: str = "full") -> RunReport:
        if mode not in ("full", "incremental"):
            raise ValueError(mode)
        report = RunReport(run_id=f"run-{utcnow()}-{uuid.uuid4().hex[:6]}", mode=mode)
        self.now = utcnow()
        self.report = report
        conn = self.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("INSERT INTO ingestion_run (run_id, mode, started_at, status) VALUES (?,?,?,?)",
                         (report.run_id, mode, self.now, "running"))
            cursor_row = conn.execute("SELECT position FROM sync_cursor WHERE source_system='erp'").fetchone()
            if mode == "incremental" and cursor_row is None:
                report.stats["fallback_to_full"] += 1
                mode = "full"
            erp_head = self.erp.max_seq()
            if mode == "full":
                self._run_full()
            else:
                self._run_incremental(cursor_row["position"], erp_head)
            self._load_reference_data()
            inf = inference.recompute(conn, self.g, report.run_id, self.now)
            for k, v in inf.items():
                report.stats[f"inferred_{k}"] += v
            conn.execute(
                "INSERT INTO sync_cursor VALUES ('erp', ?, ?) ON CONFLICT(source_system) DO UPDATE "
                "SET position = excluded.position, updated_at = excluded.updated_at", (erp_head, self.now))
            conn.execute("UPDATE ingestion_run SET finished_at=?, status='succeeded', stats=? WHERE run_id=?",
                         (utcnow(), json.dumps(report.stats), report.run_id))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return report

    def _run_full(self) -> None:
        for system, entity in PROCESSING_ORDER:
            records = list(self.erp.extract_all(entity) if system == "erp" else self.files.extract_all(system, entity))
            seen = {r.source_id for r in records}
            for rec in self._deduplicate(records):
                self._process(rec)
            # Suppressions : enregistrements connus, non supprimés, absents de l'instantané.
            for row in self.conn.execute(
                "SELECT source_id FROM source_record WHERE source_system=? AND source_entity=? AND deleted_at IS NULL",
                (system, entity)).fetchall():
                if row["source_id"] not in seen:
                    self._delete(Ref(system, entity, row["source_id"]))

    def _run_incremental(self, since: int, head: int) -> None:
        # ERP : capture de changements (CDC). Dernier événement par clé ; ordre de dépendance.
        latest: dict[tuple[str, str], str] = {}
        for ch in self.erp.changes_since(since):
            if ch["SEQ"] <= head:
                latest[(ch["TABLE_NAME"], ch["RECORD_KEY"])] = ch["OP"]
        self.report.stats["cdc_events"] += len(latest)
        order = {e: i for i, (_, e) in enumerate(PROCESSING_ORDER)}
        for (table, key), op in sorted(latest.items(), key=lambda kv: order.get(kv[0][0], 99)):
            if table == "ERP_FX_RATE":
                continue  # rechargé intégralement par _load_reference_data
            rec = None if op == "D" else self.erp.fetch(table, key)
            if rec is None:
                self._delete(Ref("erp", table, key))
            else:
                self._process(rec)
        # Sources fichiers : instantanés complets comparés par empreinte (idempotent).
        for system, entity in PROCESSING_ORDER:
            if system == "erp":
                continue
            records = list(self.files.extract_all(system, entity))
            seen = {r.source_id for r in records}
            for rec in self._deduplicate(records):
                self._process(rec)
            for row in self.conn.execute(
                "SELECT source_id FROM source_record WHERE source_system=? AND source_entity=? AND deleted_at IS NULL",
                (system, entity)).fetchall():
                if row["source_id"] not in seen:
                    self._delete(Ref(system, entity, row["source_id"]))

    # ------------------------------------------------------------------ déduplication
    def _deduplicate(self, records: list[SourceRecord]) -> list[SourceRecord]:
        """CRM : un seul contact par email normalisé (le plus récent) ; les autres sont
        rattachés au même client (id_map) mais ne produisent pas de faits."""
        if not records or records[0].system != "crm":
            return records
        groups: dict[str, list[SourceRecord]] = defaultdict(list)
        out = []
        for r in records:
            email = (r.payload.get("email") or "").strip().lower()
            if email:
                groups[email].append(r)
            else:
                out.append(r)
        for email, recs in groups.items():
            recs.sort(key=lambda r: (r.payload.get("updated_at") or "", r.source_id))
            survivor = recs[-1]
            out.append(survivor)
            for dup in recs[:-1]:
                self._record_duplicate(dup, survivor)
        return out

    def _record_duplicate(self, dup: SourceRecord, survivor: SourceRecord) -> None:
        mapped = map_record(survivor, self.reg.operations.state_from_erp)
        cid = self._find_customer_by_email(mapped.match_email)
        prev = self._source_record(dup)
        if prev and prev["content_hash"] == dup.content_hash and prev["deleted_at"] is None:
            return
        self._issue(dup, "duplicate", f"doublon CRM de {survivor.source_id} (email identique) ; version la plus récente retenue")
        # Un doublon précédemment survivant perd ses faits au profit du nouveau survivant.
        self._retract_record_facts(Ref(dup.system, dup.entity, dup.source_id), dup.payload.get("updated_at"))
        if cid:
            self._map_id(Ref(dup.system, dup.entity, dup.source_id), cid, "ID-EMAIL-DUPLICATE", 1.0)
        self._upsert_source_record(dup, cid)

    # ------------------------------------------------------------------ traitement unitaire
    def _process(self, rec: SourceRecord) -> None:
        prev = self._source_record(rec)
        if prev and prev["content_hash"] == rec.content_hash and prev["deleted_at"] is None:
            self.report.stats["unchanged"] += 1
            return
        try:
            mapped = map_record(rec, self.reg.operations.state_from_erp)
        except (KeyError, ValueError) as exc:
            self._issue(rec, "mapping_error", f"correspondance impossible : {exc!r}")
            return
        concept = self.g.concepts[mapped.concept]
        errors = validate_props(concept, mapped.props, anchor=mapped.anchor)
        if mapped.concept == "OrderLine":
            q, r = mapped.props.get("quantity"), mapped.props.get("returned_quantity")
            if isinstance(q, int) and isinstance(r, int) and q >= 1 and r > q:
                errors.append("C-LINE-RETURNS : returned_quantity > quantity")
        if errors:
            # Quarantaine : le dernier état valide (s'il existe) est conservé ; l'empreinte est
            # mémorisée pour ne pas retraiter un enregistrement inchangé.
            self._issue(rec, "validation", "; ".join(errors))
            self._upsert_source_record(rec, prev_cid(prev))
            return
        try:
            cid = self._resolve_identity(mapped)
            rel_ids = [(ra, self._resolve_ref(ra.subject, mapped, cid), self._resolve_ref(ra.object, mapped, cid))
                       for ra in mapped.relations]
        except _Skip:
            return
        self._upsert_entity(mapped, cid)
        self._project_core(mapped, cid, rel_ids)
        self._diff_facts(mapped, cid, rel_ids)
        self._upsert_source_record(rec, cid)
        self.report.stats["created" if prev is None else "updated"] += 1

    # ------------------------------------------------------------------ identité
    def _resolve_identity(self, m: MappedRecord) -> str:
        ref = m.self_ref
        row = self.conn.execute(
            "SELECT canonical_id FROM id_map WHERE source_system=? AND source_entity=? AND source_id=?",
            (ref.system, ref.entity, ref.source_id)).fetchone()
        concept = self.g.concepts[m.concept]
        if m.anchor:
            if row:
                return row["canonical_id"]
            cid = mint_canonical_id(concept.id_prefix, ref)
            if m.concept == "Customer" and m.match_email:
                other = self._find_customer_by_email(m.match_email)
                if other and other != cid:
                    self._issue(m.record, "identity_conflict",
                                f"email déjà porté par {other} (C-CUSTOMER-EMAIL-UNIQUE) ; fusion manuelle requise")
                    raise _Skip
            self._upsert_entity(m, cid)
            self._map_id(ref, cid, "ANCHOR", 1.0)
            return cid
        # Enrichissement (CRM) : rapprochement par email, sinon candidat par nom+pays.
        cid = self._find_customer_by_email(m.match_email)
        if cid:
            if not row or row["canonical_id"] != cid:
                self._map_id(ref, cid, "ID-EMAIL", 1.0)
            return cid
        candidate = None
        if m.match_name:
            for c in self.conn.execute(
                    "SELECT customer_id, name FROM customer WHERE deleted_at IS NULL AND country IS ?", (m.match_country,)):
                if _norm(c["name"]) == m.match_name:
                    candidate = c["customer_id"]
                    break
        self._retract_record_facts(ref, m.business_ts)
        if candidate:
            self._issue(m.record, "identity_review",
                        f"candidat {candidate} (même raison sociale et pays, email différent) — à valider (ID-NAME-COUNTRY)")
            self._insert_fact(candidate, "possibleCrmMatch", None, json.dumps(f"crm:{ref.source_id}"), {}, ref,
                              "inference", m.business_ts, rule_id="ID-NAME-COUNTRY",
                              derived_from=json.dumps([{"source_record": f"{ref.system}/{ref.entity}/{ref.source_id}"}]),
                              status="pending_review")
            self._upsert_source_record(m.record, None)
        else:
            self._issue(m.record, "not_a_customer", "contact CRM sans compte ERP : Prospect, non importé comme Client")
            self._upsert_source_record(m.record, None)
        raise _Skip

    def _find_customer_by_email(self, email: str | None) -> str | None:
        if not email:
            return None
        row = self.conn.execute("SELECT customer_id FROM customer WHERE email=? AND deleted_at IS NULL", (email,)).fetchone()
        return row["customer_id"] if row else None

    def _resolve_ref(self, ref: Ref, m: MappedRecord, own_id: str) -> str:
        if ref == m.self_ref:
            return own_id
        row = self.conn.execute(
            "SELECT m.canonical_id FROM id_map m JOIN entity e ON e.canonical_id = m.canonical_id "
            "WHERE m.source_system=? AND m.source_entity=? AND m.source_id=? AND e.deleted_at IS NULL",
            (ref.system, ref.entity, ref.source_id)).fetchone()
        if row is None:
            self._issue(m.record, "unresolved_reference", f"référence introuvable : {ref.system}/{ref.entity}/{ref.source_id}")
            raise _Skip
        return row["canonical_id"]

    def _map_id(self, ref: Ref, cid: str, rule: str, confidence: float) -> None:
        self.conn.execute(
            "INSERT INTO id_map VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(source_system, source_entity, source_id) "
            "DO UPDATE SET canonical_id=excluded.canonical_id, match_rule=excluded.match_rule, "
            "confidence=excluded.confidence, last_seen_at=excluded.last_seen_at",
            (ref.system, ref.entity, ref.source_id, cid, rule, confidence, self.now, self.now))

    # ------------------------------------------------------------------ projections
    def _upsert_entity(self, m: MappedRecord, cid: str) -> None:
        if not m.anchor:
            return
        label = entity_label(m.concept, m.props, m.record)
        self.conn.execute(
            "INSERT INTO entity VALUES (?,?,?,?,?,NULL) ON CONFLICT(canonical_id) DO UPDATE SET "
            "label=excluded.label, updated_at=excluded.updated_at, deleted_at=NULL",
            (cid, m.concept, label, self.now, self.now))

    def _project_core(self, m: MappedRecord, cid: str, rel_ids) -> None:
        concept = self.g.concepts[m.concept]
        table, idcol = concept.table, _id_column(concept.table)
        cols = {k: (int(v) if isinstance(v, bool) else v) for k, v in m.props.items()}
        links: dict[str, list[tuple]] = defaultdict(list)
        for ra, s_id, o_id in rel_ids:
            proj = self.g.relations[ra.predicate].projection
            if proj.get("link"):
                links[proj["table"]].append((proj, s_id, o_id, ra.qualifiers))
            elif proj["side"] == "subject" and s_id == cid:
                cols[proj["column"]] = o_id
            elif proj["side"] == "object" and o_id == cid:
                cols[proj["column"]] = s_id
        if m.anchor:
            # Les relations mono-valuées absentes de la source sont remises à NULL.
            for rname, rdef in self.g.relations.items():
                proj = rdef.projection
                if not proj.get("link") and proj["table"] == table and proj["column"] not in cols:
                    cols[proj["column"]] = None
        names = list(cols)
        sql = (f"INSERT INTO {table} ({idcol}, {', '.join(names)}, updated_at, deleted_at) "
               f"VALUES (?, {', '.join('?' for _ in names)}, ?, NULL) ON CONFLICT({idcol}) DO UPDATE SET "
               + ", ".join(f"{n}=excluded.{n}" for n in names) + ", updated_at=excluded.updated_at"
               + (", deleted_at=NULL" if m.anchor else ""))
        if not m.anchor and not self.conn.execute(f"SELECT 1 FROM {table} WHERE {idcol}=?", (cid,)).fetchone():
            return
        self.conn.execute(sql, [cid, *cols.values(), self.now])
        for ltable, items in links.items():
            proj = items[0][0]
            self.conn.execute(f"DELETE FROM {ltable} WHERE {proj['object_column']}=?", (cid,))
            for _, s_id, o_id, q in items:
                qcols = list(q)
                self.conn.execute(
                    f"INSERT OR REPLACE INTO {ltable} ({proj['subject_column']}, {proj['object_column']}"
                    + "".join(f", {c}" for c in qcols) + ") VALUES (?, ?" + ", ?" * len(qcols) + ")",
                    [s_id, o_id, *[int(v) if isinstance(v, bool) else v for v in q.values()]])

    # ------------------------------------------------------------------ faits
    def _desired_facts(self, m: MappedRecord, cid: str, rel_ids) -> set[tuple]:
        desired = set()
        for prop, value in m.props.items():
            if value is None:
                continue
            desired.add((cid, self.g.predicate(m.concept, prop), None, json.dumps(value, ensure_ascii=False), "{}"))
        for ra, s_id, o_id in rel_ids:
            desired.add((s_id, ra.predicate, o_id, None, json.dumps(ra.qualifiers, sort_keys=True)))
        return desired

    def _diff_facts(self, m: MappedRecord, cid: str, rel_ids) -> None:
        ref = m.self_ref
        desired = self._desired_facts(m, cid, rel_ids)
        existing = {}
        for f in self.conn.execute(
                "SELECT fact_id, subject, predicate, object_id, object_value, qualifiers FROM fact "
                "WHERE source_system=? AND source_entity=? AND source_id=? AND method='import' "
                "AND superseded_at IS NULL AND retracted_at IS NULL", (ref.system, ref.entity, ref.source_id)):
            existing[(f["subject"], f["predicate"], f["object_id"], f["object_value"], f["qualifiers"])] = f["fact_id"]
        desired_sp = {(d[0], d[1]) for d in desired}
        ts = m.business_ts or self.now
        for key, fid in existing.items():
            if key in desired:
                continue
            col = "superseded_at" if (key[0], key[1]) in desired_sp else "retracted_at"
            self.conn.execute(f"UPDATE fact SET {col}=?, valid_to=? WHERE fact_id=?", (self.now, ts, fid))
            self.report.stats["facts_superseded" if col == "superseded_at" else "facts_retracted"] += 1
        for d in desired - set(existing):
            self._insert_fact(d[0], d[1], d[2], d[3], json.loads(d[4]), ref, "import", m.business_ts)
            self.report.stats["facts_added"] += 1

    def _insert_fact(self, subject, predicate, object_id, object_value, qualifiers, ref: Ref, method,
                     business_ts, *, rule_id=None, derived_from=None, status="validated") -> None:
        self.conn.execute(
            "INSERT INTO fact (subject, predicate, object_id, object_value, qualifiers, source_system, source_entity,"
            " source_id, method, rule_id, derived_from, validation_status, collected_at, valid_from, run_id)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (subject, predicate, object_id, object_value, json.dumps(qualifiers, sort_keys=True), ref.system,
             ref.entity, ref.source_id, method, rule_id, derived_from, status, self.now, business_ts,
             self.report.run_id))

    def _retract_record_facts(self, ref: Ref, business_ts: str | None) -> int:
        cur = self.conn.execute(
            "UPDATE fact SET retracted_at=?, valid_to=? WHERE source_system=? AND source_entity=? AND source_id=? "
            "AND superseded_at IS NULL AND retracted_at IS NULL",
            (self.now, business_ts or self.now, ref.system, ref.entity, ref.source_id))
        self.report.stats["facts_retracted"] += cur.rowcount
        return cur.rowcount

    # ------------------------------------------------------------------ suppressions
    def _delete(self, ref: Ref) -> None:
        sr = self.conn.execute(
            "SELECT canonical_id FROM source_record WHERE source_system=? AND source_entity=? AND source_id=? "
            "AND deleted_at IS NULL", (ref.system, ref.entity, ref.source_id)).fetchone()
        if sr is None:
            return
        self._retract_record_facts(ref, None)
        cid = sr["canonical_id"]
        anchor_name = next((n for n, c in self.g.concepts.items()
                            if c.identity.anchor == {"system": ref.system, "entity": ref.entity}), None)
        if cid and anchor_name is not None:
            anchor = self.g.concepts[anchor_name]
            self.conn.execute("UPDATE entity SET deleted_at=?, updated_at=? WHERE canonical_id=?", (self.now, self.now, cid))
            self.conn.execute(f"UPDATE {anchor.table} SET deleted_at=?, updated_at=? WHERE {_id_column(anchor.table)}=?",
                              (self.now, self.now, cid))
            for rdef in self.g.relations.values():
                proj = rdef.projection
                if proj.get("link") and rdef.range == anchor_name:
                    self.conn.execute(f"DELETE FROM {proj['table']} WHERE {proj['object_column']}=?", (cid,))
                if proj.get("link") and rdef.domain == anchor_name:
                    self.conn.execute(f"DELETE FROM {proj['table']} WHERE {proj['subject_column']}=?", (cid,))
        elif cid:
            # Enrichissement supprimé : ses colonnes d'autorité reviennent à NULL.
            concept = next(c for c in self.g.concepts.values()
                           if any(sm.system == ref.system and sm.entity == ref.entity for sm in c.source_mappings))
            sm = next(sm for sm in concept.source_mappings if sm.system == ref.system and sm.entity == ref.entity)
            if sm.authority_for:
                self.conn.execute(
                    f"UPDATE {concept.table} SET " + ", ".join(f"{c}=NULL" for c in sm.authority_for)
                    + f", updated_at=? WHERE {_id_column(concept.table)}=?", (self.now, cid))
        self.conn.execute("UPDATE source_record SET deleted_at=? WHERE source_system=? AND source_entity=? AND source_id=?",
                          (self.now, ref.system, ref.entity, ref.source_id))
        self.report.stats["deleted"] += 1

    # ------------------------------------------------------------------ données de référence
    def _load_reference_data(self) -> None:
        """Taux de change (ERP) et instantanés de stock (WMS) : rechargés à l'identique (idempotent)."""
        self.conn.execute("DELETE FROM fx_rate")
        self.conn.executemany("INSERT INTO fx_rate VALUES (?,?,?)", self.erp.fx_rates())
        wh = {r["source_id"]: r["canonical_id"] for r in self.conn.execute(
            "SELECT source_id, canonical_id FROM id_map WHERE source_system='wms' AND source_entity='wms_warehouses'")}
        pr = {r["source_id"]: r["canonical_id"] for r in self.conn.execute(
            "SELECT source_id, canonical_id FROM id_map WHERE source_system='erp' AND source_entity='ERP_PRODUCT'")}
        self.conn.execute("DELETE FROM inventory_snapshot")
        n = 0
        for s in self.files.stock():
            if s["wh_code"] in wh and s["sku"] in pr:
                self.conn.execute("INSERT INTO inventory_snapshot VALUES (?,?,?,?)",
                                  (s["snapshot_date"], wh[s["wh_code"]], pr[s["sku"]], int(s["on_hand"])))
                n += 1
        self.report.stats["reference_rows"] = n

    # ------------------------------------------------------------------ utilitaires
    def _source_record(self, rec: SourceRecord):
        return self.conn.execute(
            "SELECT content_hash, deleted_at, canonical_id FROM source_record WHERE source_system=? AND source_entity=? AND source_id=?",
            (rec.system, rec.entity, rec.source_id)).fetchone()

    def _upsert_source_record(self, rec: SourceRecord, cid: str | None) -> None:
        self.conn.execute(
            "INSERT INTO source_record VALUES (?,?,?,?,?,?,?,NULL) ON CONFLICT(source_system, source_entity, source_id) "
            "DO UPDATE SET content_hash=excluded.content_hash, canonical_id=excluded.canonical_id, "
            "last_run_id=excluded.last_run_id, seen_at=excluded.seen_at, deleted_at=NULL",
            (rec.system, rec.entity, rec.source_id, rec.content_hash, cid, self.report.run_id, self.now))

    def _issue(self, rec: SourceRecord, kind: str, detail: str) -> None:
        self.conn.execute(
            "INSERT INTO ingestion_issue (run_id, source_system, source_entity, source_id, kind, detail, created_at)"
            " VALUES (?,?,?,?,?,?,?)", (self.report.run_id, rec.system, rec.entity, rec.source_id, kind, detail, self.now))
        self.report.issues.append({"source": f"{rec.system}/{rec.entity}/{rec.source_id}", "kind": kind, "detail": detail})
        self.report.stats[f"issue_{kind}"] += 1


def _norm(v: str | None) -> str | None:
    return normalize_name(v)


def prev_cid(prev) -> str | None:
    return prev["canonical_id"] if prev else None


_ID_COLUMNS = {"customer": "customer_id", "product": "product_id", "warehouse": "warehouse_id",
               "supplier": "supplier_id", "sales_order": "order_id", "order_line": "line_id"}


def _id_column(table: str) -> str:
    return _ID_COLUMNS[table]
