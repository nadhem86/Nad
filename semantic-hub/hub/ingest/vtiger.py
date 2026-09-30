"""Ingestion vTiger CRM : snapshot complet (détection des suppressions) ou delta (format de l'opération `sync`).

vTiger est l'autorité pour : Client, Contact, Produit, Fournisseur, Commande (en-tête, statut CRM), Lignes.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from ..db import utcnow
from ..ids import normalize_phone
from ..kg import IRI, KnowledgeGraph

SOURCE = "vtiger"

SOSTATUS_TO_STATUT = {"Created": "brouillon", "Approved": "confirmee", "Delivered": "livree", "Cancelled": "annulee"}
STATUT_TO_SOSTATUS = {v: k for k, v in SOSTATUS_TO_STATUT.items()}

MODULE_CONCEPT = {"Vendors": "Fournisseur", "Accounts": "Client", "Contacts": "Contact",
                  "Products": "Produit", "SalesOrder": "Commande"}
MODULE_ORDER = ["Vendors", "Accounts", "Contacts", "Products", "SalesOrder"]


def vt_datetime(s: str | None) -> str | None:
    """'2026-07-03 08:15:00' (UTC côté serveur vTiger, hypothèse H) -> '2026-07-03T08:15:00Z'."""
    if not s:
        return None
    s = s.strip().replace(" ", "T")
    return s if s.endswith("Z") else s + "Z"


class VtigerIngestor:
    def __init__(self, kg: KnowledgeGraph, run_id: str, phone_region: str = "FR"):
        self.kg = kg
        self.run_id = run_id
        self.phone_region = phone_region
        self.stats: Counter = Counter()
        self.rejected: list[dict] = []
        self.currency_map: dict[str, str] = {}
        self.links: dict[str, set[str]] = {}

    # ------------------------------------------------------------------ points d'entrée
    def ingest_snapshot(self, data: dict) -> dict:
        self._prepare(data)
        collected = vt_datetime(data.get("extracted_at")) or utcnow()
        for module in MODULE_ORDER:
            present = set()
            for rec in self._with_simulated_patches(module, data.get(module, [])):
                if self._ingest_record(module, rec, collected):
                    present.add(rec["id"])
            self._retract_absent(module, present, collected)
        return self._result()

    def ingest_delta(self, data: dict) -> dict:
        self._prepare(data)
        collected = vt_datetime(data.get("extracted_at")) or utcnow()
        for module in MODULE_ORDER:
            for rec in self._with_simulated_patches(module, data.get("updated", {}).get(module, [])):
                self._ingest_record(module, rec, collected)
            for rid in data.get("deleted", {}).get(module, []):
                self._retract(module, rid, collected, "supprime_dans_vtiger (sync.deleted)")
        return self._result()

    # ------------------------------------------------------------------ interne
    def _prepare(self, data: dict) -> None:
        self.currency_map = data.get("currency_map", {})
        for link in data.get("ProductVendorLinks", []):
            self.links.setdefault(link["productid"], set()).add(link["vendorid"])

    def _result(self) -> dict:
        return {"stats": dict(self.stats), "rejected": self.rejected}

    def _with_simulated_patches(self, module: str, records: list[dict]) -> list[dict]:
        """Démo : applique les écritures faites via la passerelle vTiger SIMULÉE, pour que la « source »
        relue reflète les actions déjà exécutées (comme le ferait le vrai vTiger)."""
        rows = self.kg.conn.execute(
            "SELECT record_id, patch FROM sim_source_write WHERE system='vtiger' AND module=? ORDER BY id", (module,)
        ).fetchall()
        if not rows:
            return records
        patched = []
        for rec in records:
            rec = dict(rec)
            for r in rows:
                patch = json.loads(r["patch"])
                if r["record_id"] == rec["id"] and patch.get("modifiedtime", "") > rec.get("modifiedtime", ""):
                    rec.update(patch)
                    self.stats["patches_simules_appliques"] += 1
            patched.append(rec)
        return patched

    def _ref(self, module: str, rid: str | None) -> IRI | None:
        if not rid:
            return None
        return IRI(self.kg.ensure_entity(MODULE_CONCEPT[module], SOURCE, module, rid))

    def _ingest_record(self, module: str, rec: dict, collected: str) -> bool:
        try:
            values, label = getattr(self, f"_map_{module}")(rec)
        except (KeyError, ValueError) as e:
            self.rejected.append({"module": module, "id": rec.get("id"), "reason": str(e)})
            self.stats["rejected"] += 1
            return False
        iri = self.kg.ensure_entity(MODULE_CONCEPT[module], SOURCE, module, rec["id"], label=label)
        s = self.kg.upsert_facts(iri, values, source=SOURCE, source_record_id=f"{module}/{rec['id']}",
                                 collected_at=collected, valid_from=vt_datetime(rec.get("modifiedtime")),
                                 run_id=self.run_id)
        self.stats.update({f"facts_{k}": v for k, v in s.items()})
        self.stats[f"records_{module}"] += 1
        if module == "SalesOrder":
            self._ingest_lines(iri, rec, collected)
        return True

    def _ingest_lines(self, order_iri: str, rec: dict, collected: str) -> None:
        present = set()
        for li in rec.get("LineItems", []):
            lid = li["lineitem_id"]
            present.add(lid)
            liri = self.kg.ensure_entity("LigneCommande", SOURCE, "LineItem", lid,
                                         label=f"{rec['salesorder_no']}-{li['sequence_no']}")
            values = {
                "numeroLigne": int(li["sequence_no"]),
                "quantite": li["quantity"],
                "prixUnitaire": li["listprice"],
                "remisePct": li.get("discount_percent") or None,
                "remiseMontant": li.get("discount_amount") or None,
                "tauxTaxe": li.get("tax1") or None,
                "ligneDe": IRI(order_iri),
                "porteSur": self._ref("Products", li["productid"]),
            }
            s = self.kg.upsert_facts(liri, values, source=SOURCE, source_record_id=f"LineItem/{lid}",
                                     collected_at=collected, valid_from=vt_datetime(rec.get("modifiedtime")),
                                     run_id=self.run_id)
            self.stats.update({f"facts_{k}": v for k, v in s.items()})
            self.stats["records_LineItem"] += 1
        # lignes supprimées de la commande
        for liri in [iri for iri, _ in self.kg.traverse(order_iri, ["^ligneDe"])]:
            sid = [x["source_id"] for x in self.kg.source_ids(liri) if x["source_type"] == "LineItem"]
            if sid and sid[0] not in present:
                self.kg.retract_entity(liri, source=SOURCE, at=collected, reason="ligne retirée de la commande")
                self.stats["retracted_LineItem"] += 1

    def _retract(self, module: str, rid: str, at: str, reason: str) -> None:
        iri = self.kg.resolve_source(SOURCE, module, rid)
        if not iri:
            return
        if module == "SalesOrder":
            for liri, _ in self.kg.traverse(iri, ["^ligneDe"]):
                self.kg.retract_entity(liri, source=SOURCE, at=at, reason="commande supprimée")
        self.kg.retract_entity(iri, source=SOURCE, at=at, reason=reason)
        self.stats[f"retracted_{module}"] += 1

    def _retract_absent(self, module: str, present: set[str], at: str) -> None:
        rows = self.kg.conn.execute(
            "SELECT si.source_id FROM source_identity si JOIN entity e ON e.iri=si.iri "
            "WHERE si.source=? AND si.source_type=? AND e.deleted_at IS NULL", (SOURCE, module)).fetchall()
        for r in rows:
            if r["source_id"] not in present:
                self._retract(module, r["source_id"], at, "absent du snapshot vtiger")

    # ------------------------------------------------------------------ mappings module -> concept
    def _map_Vendors(self, r: dict):
        return {"numeroFournisseur": r["vendor_no"], "nom": r["vendorname"], "pays": r.get("country") or None,
                "email": r.get("email") or None}, r["vendorname"]

    def _map_Accounts(self, r: dict):
        return {"numeroClient": r["account_no"], "raisonSociale": r["accountname"], "region": r["cf_region"],
                "secteur": r.get("industry") or None, "email": r.get("email1") or None,
                "telephone": normalize_phone(r.get("phone"), self.phone_region)}, r["accountname"]

    def _map_Contacts(self, r: dict):
        return {"numeroContact": r["contact_no"], "nom": r["lastname"], "prenom": r.get("firstname") or None,
                "telephone": normalize_phone(r.get("phone"), self.phone_region),
                "mobile": normalize_phone(r.get("mobile"), self.phone_region),
                "email": r.get("email") or None,
                "travaillePour": self._ref("Accounts", r.get("account_id"))}, f"{r.get('firstname', '')} {r['lastname']}".strip()

    def _map_Products(self, r: dict):
        vendors = {r["vendor_id"]} if r.get("vendor_id") else set()
        vendors |= self.links.get(r["id"], set())
        return {"reference": r["product_no"], "libelle": r["productname"], "categorie": r.get("productcategory") or None,
                "prixCatalogue": r.get("unit_price") or None, "coutUnitaire": r.get("purchase_cost") or None,
                # vTiger : discontinued = '1' signifie « produit actif » (libellé trompeur de vTiger)
                "actif": r.get("discontinued") == "1",
                "fourniPar": [self._ref("Vendors", v) for v in sorted(vendors)]}, r["productname"]

    def _map_SalesOrder(self, r: dict):
        if r["sostatus"] not in SOSTATUS_TO_STATUT:
            raise ValueError(f"sostatus inconnu : {r['sostatus']!r}")
        if r.get("currency_id") not in self.currency_map:
            raise ValueError(f"devise inconnue : {r.get('currency_id')!r}")
        statut = SOSTATUS_TO_STATUT[r["sostatus"]]
        values: dict[str, Any] = {
            "numeroCommande": r["salesorder_no"], "objet": r.get("subject") or None,
            "dateCommande": vt_datetime(r["createdtime"]), "devise": self.currency_map[r["currency_id"]],
            "statutCRM": statut, "dateConfirmation": vt_datetime(r.get("cf_date_confirmation")),
            "passeePar": self._ref("Accounts", r["account_id"]),
            "contactReferent": self._ref("Contacts", r.get("contact_id")),
        }
        if statut == "annulee":
            values["dateAnnulation"] = vt_datetime(r.get("cf_date_annulation") or r.get("modifiedtime"))
            values["motifAnnulation"] = r.get("cf_motif_annulation") or None
        return values, r["salesorder_no"]


# ---------------------------------------------------------------------- extraction depuis un vTiger réel
class VtigerWebserviceClient:
    """Client du webservice REST vTiger (portage Python de whatsapp-extractor/vtiger.js).

    NON TESTÉ contre une instance réelle dans ce dépôt : aucune instance vTiger n'est disponible.
    Les identifiants viennent des variables d'environnement (NAD_VTIGER_*), jamais du code.
    """

    def __init__(self, url: str, username: str, access_key: str, timeout: float = 30.0):
        self.base = url.rstrip("/") + "/webservice.php"
        self.username = username
        self._access_key = access_key
        self.timeout = timeout
        self.session: str | None = None

    def _call(self, params: dict, post: bool = False) -> Any:
        import urllib.parse
        import urllib.request

        data = urllib.parse.urlencode(params).encode() if post else None
        url = self.base if post else f"{self.base}?{urllib.parse.urlencode(params)}"
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=self.timeout) as resp:
            payload = json.loads(resp.read().decode())
        if not payload.get("success"):
            raise RuntimeError(f"vTiger {params.get('operation')} : {payload.get('error')}")
        return payload["result"]

    def login(self) -> None:
        import hashlib

        token = self._call({"operation": "getchallenge", "username": self.username})["token"]
        key = hashlib.md5((token + self._access_key).encode()).hexdigest()
        self.session = self._call({"operation": "login", "username": self.username, "accessKey": key}, post=True)["sessionName"]

    def query_all(self, module: str, page: int = 100) -> list[dict]:
        out, offset = [], 0
        while True:
            rows = self._call({"operation": "query", "sessionName": self.session,
                               "query": f"SELECT * FROM {module} LIMIT {offset}, {page};"})
            out += rows
            if len(rows) < page:
                return out
            offset += page

    def retrieve(self, record_id: str) -> dict:
        return self._call({"operation": "retrieve", "sessionName": self.session, "id": record_id})

    def update(self, element: dict) -> dict:
        return self._call({"operation": "revise", "sessionName": self.session, "element": json.dumps(element)}, post=True)

    def extract_snapshot(self) -> dict:
        snap: dict[str, Any] = {"extracted_at": utcnow().replace("T", " ")[:19]}
        for module in MODULE_ORDER:
            rows = self.query_all(module)
            if module == "SalesOrder":  # les lignes ne sont renvoyées que par retrieve
                rows = [self.retrieve(r["id"]) for r in rows]
            snap[module] = rows
        currencies = self._call({"operation": "query", "sessionName": self.session,
                                 "query": "SELECT id, currency_code FROM Currency;"})
        snap["currency_map"] = {c["id"]: c["currency_code"] for c in currencies}
        return snap
