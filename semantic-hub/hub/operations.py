"""Couche opérationnelle : machine à états des commandes et actions contrôlées.

Séquence d'une action :
  idempotence -> permission (RBAC, restriction agent) -> existence -> périmètre (ABAC) -> transition
  -> validations métier -> ÉCRITURE DANS LA SOURCE (autorité) -> faits KG (method=saisie) -> inférences
  -> projections -> mémorisation idempotente -> audit.
Si la source refuse ou est indisponible, rien n'est écrit dans le graphe. Si le graphe échoue APRÈS
l'écriture dans la source, une désynchronisation est journalisée ; la prochaine ingestion la résout,
car la source fait foi.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import yaml

from . import inference, projection
from .db import transaction
from .kg import IRI
from .security import AccessDenied, Principal


class ActionError(Exception):
    def __init__(self, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.code = code
        self.details = details

    def as_dict(self) -> dict:
        return {"code": self.code, "message": str(self), "details": self.details}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _vt(ts: str) -> str:
    return ts[:19].replace("T", " ")


class OrderService:
    def __init__(self, hub):
        self.hub = hub
        self.kg = hub.kg
        self.policy = hub.policy
        self.audit = hub.audit
        self.lifecycle = yaml.safe_load(hub.settings.lifecycle_path.read_text(encoding="utf-8"))
        self.actions = self.lifecycle["actions"]
        self.transitions = self.lifecycle["transitions"]

    # ------------------------------------------------------------------ consultation
    def transition_for(self, statut: str | None, action: str) -> dict | None:
        return next((t for t in self.transitions if t["from"] == statut and t["action"] == action), None)

    def available_actions(self, principal: Principal, order: str) -> list[dict]:
        statut = self.kg.get(order, "statut")
        out = []
        for name, a in self.actions.items():
            t = self.transition_for(statut, name)
            reason = None
            if t is None:
                reason = f"INVALID_TRANSITION : aucune transition '{name}' depuis '{statut}'"
            elif not self.policy.has(principal, a["permission"]):
                reason = f"FORBIDDEN : permission {a['permission']} manquante"
            elif principal.is_agent and not a.get("agent_allowed"):
                reason = "FORBIDDEN : action non autorisée aux agents IA"
            elif t.get("extra_permission") and not self.policy.has(principal, t["extra_permission"]):
                reason = f"FORBIDDEN : permission {t['extra_permission']} manquante"
            elif a["authority"] == "vtiger" and not self.policy.region_ok(principal, self.policy.client_region(self.kg, order)):
                reason = "OUT_OF_SCOPE : client hors de votre périmètre régional"
            out.append({"action": name, "label": a["label"], "to": t["to"] if t else None, "allowed": reason is None,
                        "reason": reason, "input": a["input"], "preconditions": a["preconditions"],
                        "authority": a["authority"]})
        return out

    # ------------------------------------------------------------------ exécution
    def execute(self, principal: Principal, action: str, params: dict, idempotency_key: str | None) -> dict:
        request = {"action": action, "params": params}
        try:
            result = self._execute(principal, action, params, idempotency_key, request)
        except (ActionError, AccessDenied) as e:
            code = e.code
            self.audit.record(actor=principal.id, roles=list(principal.roles),
                              kind="denied" if code in ("FORBIDDEN", "OUT_OF_SCOPE") else "action",
                              action=f"action:{action}", target=params.get("commande"), request=request,
                              outcome=code, detail=str(e))
            if isinstance(e, AccessDenied):
                raise ActionError(code, str(e)) from e
            raise
        return result

    def _execute(self, principal, action, params, key, request) -> dict:
        if action not in self.actions:
            raise ActionError("VALIDATION_ERROR", f"action inconnue : {action}", list(self.actions))
        a = self.actions[action]
        if not key or not str(key).strip():
            raise ActionError("VALIDATION_ERROR", "idempotency_key obligatoire")
        req_hash = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        prior = self.kg.conn.execute("SELECT * FROM action_request WHERE idempotency_key=?", (key,)).fetchone()
        if prior:
            if prior["request_hash"] == req_hash and prior["actor"] == principal.id:
                resp = json.loads(prior["response"])
                resp["replayed"] = True
                self.audit.record(actor=principal.id, roles=list(principal.roles), kind="action",
                                  action=f"action:{action}", target=params.get("commande"), request=request,
                                  outcome="replayed", detail={"idempotency_key": key})
                return resp
            raise ActionError("IDEMPOTENCY_CONFLICT", "clé d'idempotence déjà utilisée pour une autre requête")

        # permissions (serveur) -------------------------------------------------------------------
        self.policy.require(principal, a["permission"])
        if principal.is_agent and not a.get("agent_allowed"):
            raise ActionError("FORBIDDEN", f"l'action '{action}' n'est pas ouverte aux agents IA (politique v{self.policy.version})")

        order = params.get("commande")
        if not order or self.kg.concept_of(order) != "Commande" or (self.kg.entity(order) or {}).get("deleted_at"):
            raise ActionError("NOT_FOUND", f"commande introuvable : {order}")
        if not self.policy.region_ok(principal, self.policy.client_region(self.kg, order)):
            raise ActionError("OUT_OF_SCOPE", "client hors de votre périmètre régional")

        statut = self.kg.get(order, "statut")
        t = self.transition_for(statut, action)
        if t is None:
            allowed = [x["action"] for x in self.transitions if x["from"] == statut and x["action"]]
            raise ActionError("INVALID_TRANSITION", f"'{action}' impossible depuis l'état '{statut}'",
                              {"statut": statut, "actions_possibles": allowed})
        if t.get("extra_permission"):
            self.policy.require(principal, t["extra_permission"])

        facts, patch, module, record_id = getattr(self, f"_prepare_{action}")(principal, order, params)

        # 1) écriture dans la source (autorité) -------------------------------------------------
        gateway = self.hub.gateways[a["authority"]]
        try:
            ack = gateway.write(module, record_id, patch)
        except Exception as e:
            raise ActionError("SOURCE_UNAVAILABLE", f"écriture refusée ou impossible dans {a['authority']} : {e}") from e

        # 2) représentations dérivées ---------------------------------------------------------
        try:
            with transaction(self.kg.conn):
                self.kg.upsert_facts(order, facts, source=a["authority"], source_record_id=f"{module}/{record_id}",
                                     collected_at=ack.get("acknowledged_at") or _now(), valid_from=_now(),
                                     run_id=f"action:{key}", method="saisie", actor=principal.id, replace_missing=False)
                inference.run_all(self.kg, f"action:{key}")
            projection.rebuild(self.kg, self.hub.settings.business_timezone)
        except Exception as e:  # la source a accepté : on journalise la désynchronisation
            self.audit.record(actor=principal.id, roles=list(principal.roles), kind="desync", action=f"action:{action}",
                              target=order, request=request, outcome="SYNC_PENDING", detail=str(e))
            raise ActionError("SYNC_PENDING", "action acceptée par la source, graphe à resynchroniser") from e

        response = {
            "action": action, "commande": order, "numero": self.kg.get(order, "numeroCommande"),
            "statut_avant": statut, "statut_apres": self.kg.get(order, "statut"),
            "effets": {k: (v.value if isinstance(v, IRI) else v) for k, v in facts.items()},
            "source_ack": ack, "authority": a["authority"], "simulated_source": gateway.simulated,
            "idempotency_key": key, "replayed": False,
        }
        self.kg.conn.execute("INSERT INTO action_request VALUES (?,?,?,?,?,?,?)",
                             (key, principal.id, action, order, req_hash, json.dumps(response), _now()))
        self.audit.record(actor=principal.id, roles=list(principal.roles), kind="action", action=f"action:{action}",
                          target=order, request=request, outcome="ok", detail=response)
        return response

    # ------------------------------------------------------------------ préparation par action
    def _vtiger_id(self, order: str) -> str:
        ids = [x["source_id"] for x in self.kg.source_ids(order) if x["source"] == "vtiger"]
        if not ids:
            raise ActionError("VALIDATION_ERROR", "commande sans identifiant vTiger")
        return ids[0]

    def _lines(self, order: str) -> list[str]:
        return [l for l, _ in self.kg.traverse(order, ["^ligneDe"])]

    def _prepare_confirmer(self, principal, order, params):
        lines = self._lines(order)
        if not lines:
            raise ActionError("VALIDATION_ERROR", "la commande n'a aucune ligne")
        bad = [self.kg.entity(l)["label"] for l in lines
               if not (self.kg.get(l, "quantite") or 0) > 0 or (self.kg.get(l, "prixUnitaire") or -1) < 0]
        if bad:
            raise ActionError("VALIDATION_ERROR", "lignes invalides (quantité ou prix)", bad)
        now = _now()
        return ({"statutCRM": "confirmee", "dateConfirmation": now},
                {"sostatus": "Approved", "cf_date_confirmation": _vt(now), "modifiedtime": _vt(now)},
                "SalesOrder", self._vtiger_id(order))

    def _prepare_annuler(self, principal, order, params):
        motif = (params.get("motif") or "").strip()
        if not 5 <= len(motif) <= 500:
            raise ActionError("VALIDATION_ERROR", "motif obligatoire (5 à 500 caractères)")
        now = _now()
        return ({"statutCRM": "annulee", "dateAnnulation": now, "motifAnnulation": motif},
                {"sostatus": "Cancelled", "cf_date_annulation": _vt(now), "cf_motif_annulation": motif,
                 "modifiedtime": _vt(now)},
                "SalesOrder", self._vtiger_id(order))

    def _prepare_affecter_entrepot(self, principal, order, params):
        wh = params.get("entrepot")
        if not wh or self.kg.concept_of(wh) != "Entrepot" or (self.kg.entity(wh) or {}).get("deleted_at"):
            raise ActionError("NOT_FOUND", f"entrepôt introuvable : {wh}")
        code = self.kg.get(wh, "code")
        if not self.policy.warehouse_ok(principal, code):
            raise ActionError("OUT_OF_SCOPE", f"entrepôt {code} hors de votre périmètre")
        conn = self.kg.conn
        last = conn.execute("SELECT MAX(releve_date) FROM fct_stock WHERE entrepot_iri=?", (wh,)).fetchone()[0]
        need: dict[str, float] = {}
        for l in self._lines(order):
            p = self.kg.get(l, "porteSur")
            need[p] = need.get(p, 0) + (self.kg.get(l, "quantite") or 0)
        short = []
        for p, q in need.items():
            avail = conn.execute("SELECT COALESCE(SUM(quantite),0) FROM fct_stock WHERE entrepot_iri=? AND produit_iri=? "
                                 "AND releve_date=?", (wh, p, last)).fetchone()[0] if last else 0
            if avail < q:
                short.append({"produit": self.kg.get(p, "reference"), "demande": q, "disponible": avail,
                              "releve": last})
        if short:
            raise ActionError("STOCK_INSUFFISANT", f"stock insuffisant dans {code}", short)
        now = _now()
        so = self.kg.get(order, "numeroCommande")
        return ({"prepareeDans": IRI(wh), "dateAffectation": now, "statutLogistique": "en_preparation"},
                {"event_id": f"SIM-{so}-affectation", "salesorder_no": so, "event": "affectation", "ts_utc": now,
                 "warehouse_code": code, "carrier": "", "tracking": "", "line_sequence": "", "quantity": ""},
                "events", so)

    def _prepare_expedier(self, principal, order, params):
        carrier = (params.get("transporteur") or "").strip()
        tracking = (params.get("numero_suivi") or "").strip()
        if not carrier or not tracking:
            raise ActionError("VALIDATION_ERROR", "transporteur et numero_suivi obligatoires")
        wh = self.kg.get(order, "prepareeDans")
        if not wh:
            raise ActionError("VALIDATION_ERROR", "commande non affectée à un entrepôt")
        code = self.kg.get(wh, "code")
        if not self.policy.warehouse_ok(principal, code):
            raise ActionError("OUT_OF_SCOPE", f"entrepôt {code} hors de votre périmètre")
        now = _now()
        so = self.kg.get(order, "numeroCommande")
        return ({"dateExpedition": now, "transporteur": carrier, "numeroSuivi": tracking, "statutLogistique": "expediee"},
                {"event_id": f"SIM-{so}-expedition", "salesorder_no": so, "event": "expedition", "ts_utc": now,
                 "warehouse_code": code, "carrier": carrier, "tracking": tracking, "line_sequence": "", "quantity": ""},
                "events", so)
