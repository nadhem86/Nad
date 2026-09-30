"""Adaptateurs des systèmes sources (simulés) et correspondance vers le glossaire.

Chaque adaptateur produit des `SourceRecord` bruts ; `map_record` les traduit en
`MappedRecord` exprimé dans le vocabulaire du glossaire (concepts, propriétés, relations).
Les références entre enregistrements restent des clés SOURCES (`Ref`) ; elles sont
résolues en identifiants canoniques par le pipeline.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")


@dataclass(frozen=True)
class Ref:
    system: str
    entity: str
    source_id: str


@dataclass
class SourceRecord:
    system: str
    entity: str
    source_id: str
    payload: dict[str, Any]

    @property
    def content_hash(self) -> str:
        raw = json.dumps(self.payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()


@dataclass
class RelationAssertion:
    predicate: str
    subject: Ref
    object: Ref
    qualifiers: dict[str, Any] = field(default_factory=dict)


@dataclass
class MappedRecord:
    record: SourceRecord
    concept: str
    anchor: bool                 # l'enregistrement définit l'identité (mint d'identifiant canonique)
    props: dict[str, Any]        # propriétés du glossaire dont la source fait autorité (+ dérivées)
    relations: list[RelationAssertion]
    business_ts: str | None      # horodatage métier (valid_from) ; None = inconnu
    match_email: str | None = None
    match_name: str | None = None
    match_country: str | None = None

    @property
    def self_ref(self) -> Ref:
        return Ref(self.record.system, self.record.entity, self.record.source_id)


# Ordre de traitement : les cibles de références d'abord.
PROCESSING_ORDER = [
    ("wms", "wms_warehouses"),
    ("erp", "ERP_PRODUCT"),
    ("srm", "srm_suppliers"),
    ("erp", "ERP_CUSTOMER"),
    ("crm", "crm_contacts"),
    ("erp", "ERP_ORDER"),
    ("erp", "ERP_ORDER_LINE"),
]

ERP_KEYS = {
    "ERP_CUSTOMER": ("CUST_NO",),
    "ERP_PRODUCT": ("ITEM_NO",),
    "ERP_ORDER": ("ORD_NO",),
    "ERP_ORDER_LINE": ("ORD_NO", "LINE_NO"),
}


def normalize_email(v: str | None) -> str | None:
    return v.strip().lower() if v else None


def normalize_name(v: str | None) -> str | None:
    if not v:
        return None
    return "".join(ch for ch in v.casefold() if ch.isalnum())


def local_date(utc_iso: str) -> str:
    dt = datetime.fromisoformat(utc_iso.replace("Z", "+00:00"))
    return dt.astimezone(PARIS).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------------------
class ErpSource:
    """ERP simulé (SQLite) avec journal de changements ERP_CHANGELOG."""

    system = "erp"

    def __init__(self, path: Path):
        self.path = Path(path)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def key_of(entity: str, row: sqlite3.Row | dict) -> str:
        return "#".join(str(row[k]) for k in ERP_KEYS[entity])

    def extract_all(self, entity: str) -> Iterator[SourceRecord]:
        with self._conn() as conn:
            for row in conn.execute(f"SELECT * FROM {entity}"):  # noqa: S608 - nom de table interne
                d = {k: row[k] for k in row.keys() if k != "ROW_VERSION"}
                yield SourceRecord("erp", entity, self.key_of(entity, row), d)

    def fetch(self, entity: str, key: str) -> SourceRecord | None:
        cols = ERP_KEYS[entity]
        parts = key.split("#")
        where = " AND ".join(f"{c} = ?" for c in cols)
        with self._conn() as conn:
            row = conn.execute(f"SELECT * FROM {entity} WHERE {where}", parts).fetchone()  # noqa: S608
        if row is None:
            return None
        d = {k: row[k] for k in row.keys() if k != "ROW_VERSION"}
        return SourceRecord("erp", entity, key, d)

    def max_seq(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COALESCE(MAX(SEQ), 0) FROM ERP_CHANGELOG").fetchone()[0]

    def changes_since(self, seq: int) -> list[sqlite3.Row]:
        with self._conn() as conn:
            return conn.execute(
                "SELECT SEQ, TABLE_NAME, RECORD_KEY, OP FROM ERP_CHANGELOG WHERE SEQ > ? ORDER BY SEQ", (seq,)
            ).fetchall()

    def fx_rates(self) -> list[tuple[str, str, float]]:
        with self._conn() as conn:
            return [tuple(r) for r in conn.execute("SELECT RATE_DATE, CURRENCY, RATE_TO_EUR FROM ERP_FX_RATE")]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


class FileSources:
    """CRM (CSV), WMS (CSV) et SRM (JSON) : instantanés complets, comparés par empreinte."""

    def __init__(self, sources_dir: Path):
        self.dir = Path(sources_dir)

    def extract_all(self, system: str, entity: str) -> Iterator[SourceRecord]:
        if (system, entity) == ("crm", "crm_contacts"):
            for r in _read_csv(self.dir / "crm_contacts.csv"):
                yield SourceRecord("crm", entity, r["contact_id"], r)
        elif (system, entity) == ("wms", "wms_warehouses"):
            for r in _read_csv(self.dir / "wms_warehouses.csv"):
                yield SourceRecord("wms", entity, r["wh_code"], r)
        elif (system, entity) == ("srm", "srm_suppliers"):
            doc = json.loads((self.dir / "srm_suppliers.json").read_text(encoding="utf-8"))
            for s in doc["suppliers"]:
                yield SourceRecord("srm", entity, s["supplier_ref"], {**s, "_exported_at": doc.get("exported_at")})
        else:
            raise KeyError((system, entity))

    def stock(self) -> list[dict[str, str]]:
        return _read_csv(self.dir / "wms_stock.csv")


# --------------------------------------------------------------------------------------
# Correspondance source → glossaire
# --------------------------------------------------------------------------------------
def _num(v):
    if v is None or v == "":
        return None
    return v


def _bool(v) -> bool | None:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "y", "yes", "oui")


def map_record(rec: SourceRecord, state_from_erp) -> MappedRecord:
    p = rec.payload
    key = (rec.system, rec.entity)
    if key == ("erp", "ERP_CUSTOMER"):
        return MappedRecord(rec, "Customer", True, {
            "name": p["NAME"], "email": normalize_email(p["EMAIL"]), "country": p["COUNTRY"],
            "region": p["REGION"], "status": {"A": "active", "I": "inactive"}.get(p["STATUS"], p["STATUS"]),
            "note": p["NOTE"],
        }, [], p["UPDATED_AT"], match_email=normalize_email(p["EMAIL"]))
    if key == ("crm", "crm_contacts"):
        return MappedRecord(rec, "Customer", False, {
            "segment": p.get("segment") or None, "phone": p.get("phone") or None,
            "marketing_consent": _bool(p.get("consent_marketing")),
        }, [], p.get("updated_at"), match_email=normalize_email(p.get("email")),
            match_name=normalize_name(p.get("full_name")), match_country=p.get("country"))
    if key == ("erp", "ERP_PRODUCT"):
        return MappedRecord(rec, "Product", True, {
            "sku": p["ITEM_NO"], "name": p["LABEL"], "category": p["CATEGORY"],
            "list_price_eur": _num(p["LIST_PRICE"]), "std_cost_eur": _num(p["STD_COST"]),
            "active": _bool(p["ACTIVE"]),
        }, [], p["UPDATED_AT"])
    if key == ("wms", "wms_warehouses"):
        return MappedRecord(rec, "Warehouse", True, {
            "code": p["wh_code"], "name": p["name"], "city": p["city"], "region": p["region"],
            "active": _bool(p["active"]), "capacity": int(p["capacity"]) if p.get("capacity") else None,
        }, [], None)
    if key == ("srm", "srm_suppliers"):
        me = Ref("srm", "srm_suppliers", p["supplier_ref"])
        rels = [
            RelationAssertion("suppliedBy", Ref("erp", "ERP_PRODUCT", s["item_no"]), me,
                              {"lead_time_days": s.get("lead_time_days"), "is_primary": bool(s.get("is_primary"))})
            for s in p.get("supplies", [])
        ]
        return MappedRecord(rec, "Supplier", True, {
            "name": p["name"], "country": p["country"], "contact_email": normalize_email(p.get("contact_email")),
        }, rels, p.get("_exported_at"))
    if key == ("erp", "ERP_ORDER"):
        me = Ref("erp", "ERP_ORDER", p["ORD_NO"])
        rels = [RelationAssertion("placedBy", me, Ref("erp", "ERP_CUSTOMER", p["CUST_NO"]))]
        if p.get("WAREHOUSE_CODE"):
            rels.append(RelationAssertion("fulfilledFrom", me, Ref("wms", "wms_warehouses", p["WAREHOUSE_CODE"])))
        return MappedRecord(rec, "Order", True, {
            "erp_order_no": p["ORD_NO"], "status": state_from_erp(p["STATUS_CD"]), "currency": p["CURRENCY"],
            "ordered_at": p["ORDER_TS"], "order_date_local": local_date(p["ORDER_TS"]),
            "confirmed_at": p["CONFIRMED_TS"], "shipped_at": p["SHIPPED_TS"], "delivered_at": p["DELIVERED_TS"],
            "cancelled_at": p["CANCELLED_TS"], "cancel_reason": p["CANCEL_REASON"], "tracking_number": p["TRACKING_NO"],
        }, rels, p["UPDATED_AT"])
    if key == ("erp", "ERP_ORDER_LINE"):
        me = Ref("erp", "ERP_ORDER_LINE", rec.source_id)
        rels = [
            RelationAssertion("hasLine", Ref("erp", "ERP_ORDER", p["ORD_NO"]), me),
            RelationAssertion("refersToProduct", me, Ref("erp", "ERP_PRODUCT", p["ITEM_NO"])),
        ]
        return MappedRecord(rec, "OrderLine", True, {
            "line_no": p["LINE_NO"], "quantity": p["QTY"], "unit_price": p["UNIT_PRICE"],
            "discount_pct": p["DISCOUNT_PCT"], "tax_rate": p["TAX_RATE"], "unit_cost_eur": _num(p["UNIT_COST"]),
            "returned_quantity": p["RETURNED_QTY"] or 0,
        }, rels, p["UPDATED_AT"])
    raise KeyError(key)


def entity_label(concept: str, props: dict[str, Any], rec: SourceRecord) -> str:
    if concept == "Order":
        return props["erp_order_no"]
    if concept == "OrderLine":
        return rec.source_id
    if concept == "Product":
        return f"{props['sku']} {props['name']}"
    if concept == "Warehouse":
        return f"{props['code']} {props['name']}"
    return props.get("name") or rec.source_id
