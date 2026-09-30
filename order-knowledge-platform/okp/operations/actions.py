"""Actions métier contrôlées sur les Commandes (contrats : models/operations.yaml).

Ordre d'exécution : validation du contrat d'entrée → idempotence → résolution de la
commande → autorisation (rôle, canal, périmètre) → transition → préconditions →
exécution par le système faisant autorité (ERP) → resynchronisation ciblée des
représentations dérivées (core, KG, inférences) → journalisation (action_log + audit).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Callable

from pydantic import ConfigDict, Field, ValidationError, create_model

from okp.db import utcnow
from okp.operations import preconditions as pc
from okp.operations.gateway import ErpGateway, SourceConflict, SourceUnavailable
from okp.registry import Registry
from okp.security.audit import AuditLog
from okp.security.policy import AccessError, PolicyEngine, Principal

# Effets demandés à l'ERP pour chaque action ("@now" = horodatage fixé par l'ERP).
ERP_EFFECTS: dict[str, Callable[[dict], dict]] = {
    "confirm_order": lambda p: {"CONFIRMED_TS": "@now"},
    "cancel_order": lambda p: {"CANCELLED_TS": "@now", "CANCEL_REASON": p["reason"]},
    "assign_warehouse": lambda p: {"WAREHOUSE_CODE": p["_warehouse"]["code"]},
    "ship_order": lambda p: {"SHIPPED_TS": "@now", "TRACKING_NO": p["tracking_number"]},
    "deliver_order": lambda p: {"DELIVERED_TS": "@now"},
}
# Codes d'erreur dont le résultat n'est PAS mémorisé pour l'idempotence (transitoires ou sécurité).
NOT_REPLAYED = {"FORBIDDEN", "NOT_FOUND", "SOURCE_UNAVAILABLE", "VALIDATION_ERROR", "CONFLICT"}


class ActionError(Exception):
    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "details": self.details}


def build_input_model(name: str, spec: dict[str, dict[str, Any]]):
    fields = {}
    for fname, f in spec.items():
        constraints = {k: f[k] for k in ("min_length", "max_length", "pattern") if k in f}
        if fname == "idempotency_key":
            constraints.update(min_length=8, max_length=128)
        default = ... if f.get("required") else None
        fields[fname] = (str if f.get("required") else str | None,
                         Field(default, description=f.get("description"), **constraints))
    return create_model(f"{name}_input", __config__=ConfigDict(extra="forbid"), **fields)


class ActionService:
    def __init__(self, conn: sqlite3.Connection, registry: Registry, policy: PolicyEngine, audit: AuditLog,
                 gateway: ErpGateway, sync: Callable[[], dict]):
        self.conn, self.reg, self.policy, self.audit, self.gateway, self.sync = conn, registry, policy, audit, gateway, sync
        self.ops = registry.operations
        self.input_models = {n: build_input_model(n, a.input) for n, a in self.ops.actions.items()}
        missing = {c for a in self.ops.actions.values() for c in a.preconditions} - set(pc.REGISTRY)
        if missing:
            raise ValueError(f"préconditions non implémentées : {missing}")
        if set(ERP_EFFECTS) != set(self.ops.actions):
            raise ValueError("effets ERP et actions déclarées divergent")

    # ------------------------------------------------------------------ contexte
    def order_context(self, order_ref: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT o.*, c.region AS customer_region, c.status AS customer_status, w.code AS warehouse_code "
            "FROM sales_order o LEFT JOIN customer c ON c.customer_id=o.customer_id "
            "LEFT JOIN warehouse w ON w.warehouse_id=o.warehouse_id "
            "WHERE o.deleted_at IS NULL AND (o.order_id = ? OR o.erp_order_no = ?)",
            (order_ref.upper(), order_ref.upper())).fetchone()
        if row is None:
            return None
        lines = [dict(r) for r in self.conn.execute(
            "SELECT l.line_no, l.quantity, l.product_id, p.sku, p.active AS product_active FROM order_line l "
            "JOIN product p ON p.product_id=l.product_id WHERE l.order_id=? AND l.deleted_at IS NULL ORDER BY l.line_no",
            (row["order_id"],))]
        return {**dict(row), "lines": lines}

    def _warehouse(self, ref: str | None) -> dict | None:
        if not ref:
            return None
        r = self.conn.execute("SELECT * FROM warehouse WHERE deleted_at IS NULL AND (warehouse_id=? OR code=?)",
                              (ref.upper(), ref.upper())).fetchone()
        return dict(r) if r else None

    # ------------------------------------------------------------------ évaluation sans effet
    def evaluate(self, principal: Principal, action: str, ctx: dict, params: dict) -> dict[str, Any]:
        adef = self.ops.actions[action]
        target = self.ops.target_state(action, ctx["status"])
        try:
            self.policy.authorize_action(principal, action, order_region=ctx["customer_region"],
                                         order_warehouse_code=ctx["warehouse_code"],
                                         target_warehouse_code=(params.get("_warehouse") or {}).get("code"))
            allowed, why = True, None
        except AccessError as exc:
            allowed, why = False, exc.message
        checks = []
        if target is not None:
            for name in adef.preconditions:
                if action == "assign_warehouse" and "_warehouse" not in params and name in ("warehouse_active", "stock_sufficient"):
                    checks.append({"precondition": name, "satisfied": None, "detail": "dépend de l'entrepôt choisi"})
                    continue
                ok, detail = pc.REGISTRY[name](self.conn, ctx, params)
                checks.append({"precondition": name, "satisfied": ok, "detail": detail})
        return {
            "action": action, "label": adef.label, "permission": adef.permission,
            "allowed_by_policy": allowed, "policy_reason": why,
            "transition_valid": target is not None, "from_state": ctx["status"], "to_state": target,
            "preconditions": checks,
            "executable": allowed and target is not None and all(c["satisfied"] is not False for c in checks),
            "input_contract": {k: v for k, v in adef.input.items()},
        }

    def available(self, principal: Principal, order_ref: str) -> list[dict]:
        ctx = self.order_context(order_ref)
        if ctx is None or not self.policy.can_see_entity(self.conn, principal, "Order", ctx["order_id"]):
            raise ActionError("NOT_FOUND", "commande introuvable ou hors périmètre")
        return [self.evaluate(principal, a, ctx, {}) for a in self.ops.actions]

    # ------------------------------------------------------------------ exécution
    def execute(self, principal: Principal, action: str, raw: dict[str, Any]) -> dict[str, Any]:
        if action not in self.ops.actions:
            raise ActionError("VALIDATION_ERROR", f"action inconnue : {action}", available=list(self.ops.actions))
        try:
            params = self.input_models[action](**raw).model_dump()
        except ValidationError as exc:
            self.audit.record(principal, f"action.{action}", raw.get("order_id"), "failure",
                              {"code": "VALIDATION_ERROR", "errors": exc.errors(include_input=False, include_url=False)})
            raise ActionError("VALIDATION_ERROR", "entrée invalide",
                              errors=exc.errors(include_input=False, include_url=False)) from None
        key = params.pop("idempotency_key")
        req_hash = hashlib.sha256(json.dumps({"a": action, "p": params, "u": principal.id}, sort_keys=True).encode()).hexdigest()
        prev = self.conn.execute("SELECT * FROM action_log WHERE idempotency_key=?", (key,)).fetchone()
        if prev is not None:
            if prev["principal_id"] != principal.id or prev["request_hash"] != req_hash:
                self.audit.record(principal, f"action.{action}", params["order_id"], "deny", {"code": "IDEMPOTENCY_CONFLICT"})
                raise ActionError("IDEMPOTENCY_CONFLICT", "clé d'idempotence déjà utilisée pour une autre requête")
            stored = json.loads(prev["response"])
            self.audit.record(principal, f"action.{action}", params["order_id"], "info", {"replayed": True, "key": key})
            if prev["outcome"] == "failed":
                raise ActionError(stored["code"], stored["message"], replayed=True, **stored.get("details", {}))
            return {**stored, "replayed": True}

        try:
            result = self._execute(principal, action, params)
        except ActionError as exc:
            decision = "deny" if exc.code in ("FORBIDDEN", "NOT_FOUND") else "failure"
            self.audit.record(principal, f"action.{action}", params["order_id"], decision,
                              {"code": exc.code, "message": exc.message, "key": key})
            if exc.code not in NOT_REPLAYED:
                self._log(key, action, principal, req_hash, "failed", exc.as_dict())
            raise
        audit_id = self.audit.record(principal, f"action.{action}", result["order_id"], "success",
                                     {"key": key, "from": result["previous_status"], "to": result["new_status"],
                                      "sync_status": result["sync_status"],
                                      **({"reason": params["reason"]} if action == "cancel_order" else {})})
        result["audit_id"] = audit_id
        self._log(key, action, principal, req_hash, "succeeded", result)
        return {**result, "replayed": False}

    def _execute(self, principal: Principal, action: str, params: dict) -> dict[str, Any]:
        ctx = self.order_context(params["order_id"])
        if ctx is None:
            raise ActionError("NOT_FOUND", "commande introuvable ou hors périmètre")
        if action == "assign_warehouse":
            params["_warehouse"] = self._warehouse(params["warehouse_id"])
            if params["_warehouse"] is None:
                raise ActionError("NOT_FOUND", f"entrepôt introuvable : {params['warehouse_id']}")
        ev = self.evaluate(principal, action, ctx, params)
        if not ev["allowed_by_policy"]:
            # Rejouer la décision d'accès pour obtenir le bon code (FORBIDDEN / NOT_FOUND).
            try:
                self.policy.authorize_action(principal, action, order_region=ctx["customer_region"],
                                             order_warehouse_code=ctx["warehouse_code"],
                                             target_warehouse_code=(params.get("_warehouse") or {}).get("code"))
            except AccessError as exc:
                raise ActionError(exc.code, exc.message, **exc.details) from None
        if not ev["transition_valid"]:
            allowed_from = [t["from"] for t in self.ops.transitions if t["action"] == action]
            raise ActionError("INVALID_TRANSITION", f"{action} impossible depuis le statut {ctx['status']}",
                              current_status=ctx["status"], allowed_from=allowed_from)
        failed = [c for c in ev["preconditions"] if c["satisfied"] is False]
        if failed:
            raise ActionError("PRECONDITION_FAILED", "préconditions non satisfaites", failed=failed)
        try:
            receipt = self.gateway.transition(ctx["erp_order_no"], self.ops.erp_code(ctx["status"]),
                                              self.ops.erp_code(ev["to_state"]), ERP_EFFECTS[action](params))
        except SourceConflict as exc:
            raise ActionError("CONFLICT", str(exc), hint="resynchroniser puis réessayer avec une nouvelle clé") from None
        except SourceUnavailable as exc:
            raise ActionError("SOURCE_UNAVAILABLE", str(exc)) from None
        # Resynchronisation ciblée : même chemin que l'ingestion (une seule logique de projection).
        try:
            sync = self.sync()
            sync_status = "synced"
        except Exception as exc:  # noqa: BLE001 — l'action a réussi dans la source : on file la reprise
            self.conn.execute("INSERT INTO sync_outbox (reason, created_at, error) VALUES (?,?,?)",
                              (f"{action} {ctx['erp_order_no']}", utcnow(), repr(exc)))
            sync, sync_status = None, "pending"
        after = self.order_context(ctx["order_id"])
        return {
            "action": action, "order_id": ctx["order_id"], "erp_order_no": ctx["erp_order_no"],
            "previous_status": ctx["status"],
            "new_status": after["status"] if sync_status == "synced" else ev["to_state"],
            "authority": {"system": self.ops.authority, "receipt": receipt},
            "sync_status": sync_status, "sync_run": sync.get("run_id") if sync else None,
            "preconditions": ev["preconditions"],
        }

    def _log(self, key, action, principal, req_hash, outcome, response) -> None:
        self.conn.execute("INSERT INTO action_log VALUES (?,?,?,?,?,?,?)",
                          (key, action, principal.id, req_hash, outcome, json.dumps(response, default=str), utcnow()))
