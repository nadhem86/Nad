"""Authentification, principaux et moteur de politique (RBAC + attributs), côté serveur."""

from __future__ import annotations

import hashlib
import hmac
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from okp.registry import MetricDef, Registry

CHANNELS = ("api", "agent", "cli")


class AccessError(Exception):
    code = "FORBIDDEN"

    def __init__(self, message: str, **details):
        super().__init__(message)
        self.message = message
        self.details = details


class Forbidden(AccessError):
    code = "FORBIDDEN"


class NotFound(AccessError):
    """Entité absente OU hors périmètre : on ne divulgue pas l'existence."""
    code = "NOT_FOUND"


class Unauthenticated(AccessError):
    code = "UNAUTHENTICATED"


@dataclass(frozen=True)
class Principal:
    id: str
    display_name: str
    roles: tuple[str, ...]
    attributes: dict[str, Any] = field(default_factory=dict, hash=False, compare=False)
    channel: str = "api"

    def on_channel(self, channel: str) -> "Principal":
        if channel not in CHANNELS:
            raise ValueError(channel)
        return Principal(self.id, self.display_name, self.roles, self.attributes, channel)


class Authenticator:
    """Clés API → principaux. Seules les empreintes SHA-256 sont stockées ; comparaison à temps constant."""

    def __init__(self, principals_file: Path, registry: Registry):
        doc = yaml.safe_load(Path(principals_file).read_text(encoding="utf-8"))
        self._entries = []
        for p in doc["principals"]:
            unknown = set(p["roles"]) - set(registry.policies.roles)
            if unknown:
                raise ValueError(f"rôles inconnus pour {p['id']} : {unknown}")
            self._entries.append((p["api_key_sha256"], Principal(
                p["id"], p.get("display_name", p["id"]), tuple(p["roles"]), dict(p.get("attributes") or {}))))

    def authenticate(self, api_key: str | None, channel: str) -> Principal:
        if not api_key:
            raise Unauthenticated("clé API manquante")
        digest = hashlib.sha256(api_key.encode()).hexdigest()
        found = None
        for stored, principal in self._entries:
            if hmac.compare_digest(stored, digest):
                found = principal
        if found is None:
            raise Unauthenticated("clé API invalide")
        return found.on_channel(channel)

    def principal(self, principal_id: str, channel: str = "cli") -> Principal:
        for _, p in self._entries:
            if p.id == principal_id:
                return p.on_channel(channel)
        raise Unauthenticated(f"principal inconnu : {principal_id}")


class PolicyEngine:
    def __init__(self, registry: Registry):
        self.reg = registry
        self.pol = registry.policies

    # ---------------------------------------------------------------- permissions
    def denied_by_channel(self, p: Principal) -> set[str]:
        return set(self.pol.channels.get(p.channel, {}).get("deny", []))

    def permissions(self, p: Principal) -> set[str]:
        perms: set[str] = set()
        for r in p.roles:
            perms.update(self.pol.roles[r].permissions)
        return perms

    def has(self, p: Principal, perm: str) -> bool:
        if perm in self.denied_by_channel(p):
            return False
        perms = self.permissions(p)
        if "*" in perms or perm in perms:
            return True
        family = perm.split(":", 1)[0]
        return f"{family}:*" in perms

    def require(self, p: Principal, perm: str) -> None:
        if not self.has(p, perm):
            raise Forbidden(f"permission manquante : {perm} (canal {p.channel})", permission=perm)

    # ---------------------------------------------------------------- portées (ABAC)
    def _scope(self, p: Principal, attr: str) -> set[str] | None:
        """None = pas de restriction. Une restriction s'applique si TOUS les rôles du principal
        sont restreints par cet attribut (un rôle non restreint lève la restriction)."""
        if "*" in self.permissions(p):
            return None
        roles = [self.pol.roles[r] for r in p.roles]
        if roles and all(attr in r.scope_attributes for r in roles):
            return set(p.attributes.get(attr) or [])
        return None

    def region_scope(self, p: Principal) -> set[str] | None:
        return self._scope(p, "regions")

    def warehouse_scope(self, p: Principal) -> set[str] | None:
        return self._scope(p, "warehouses")

    def entity_region(self, conn: sqlite3.Connection, concept: str, cid: str) -> str | None:
        if concept == "Customer":
            sql = "SELECT region FROM customer WHERE customer_id=?"
        elif concept == "Order":
            sql = "SELECT c.region FROM sales_order o JOIN customer c ON c.customer_id=o.customer_id WHERE o.order_id=?"
        elif concept == "OrderLine":
            sql = ("SELECT c.region FROM order_line l JOIN sales_order o ON o.order_id=l.order_id "
                   "JOIN customer c ON c.customer_id=o.customer_id WHERE l.line_id=?")
        else:
            return None
        row = conn.execute(sql, (cid,)).fetchone()
        return row[0] if row else None

    def can_see_entity(self, conn: sqlite3.Connection, p: Principal, concept: str, cid: str) -> bool:
        if not self.has(p, f"entity.read:{concept}"):
            return False
        regions = self.region_scope(p)
        if regions is None or concept not in ("Customer", "Order", "OrderLine"):
            return True
        return self.entity_region(conn, concept, cid) in regions

    def field_visible(self, p: Principal, sensitivity: str) -> bool:
        if sensitivity == "pii":
            return self.has(p, "field.read:pii")
        if sensitivity == "confidential":
            return self.has(p, "field.read:confidential")
        return True

    # ---------------------------------------------------------------- métriques
    def authorize_metric(self, p: Principal, name: str, m: MetricDef) -> dict[str, list[str]]:
        """Vérifie l'accès et renvoie les filtres de sécurité à injecter (ligne à ligne)."""
        self.require(p, f"metric.read:{name}")
        if m.sensitivity in ("confidential", "pii") and not self.field_visible(p, m.sensitivity):
            raise Forbidden(f"la métrique {name} est {m.sensitivity}", permission=f"field.read:{m.sensitivity}")
        filters = {}
        regions = self.region_scope(p)
        if regions is not None:
            filters["customer.region"] = sorted(regions)
        return filters

    # ---------------------------------------------------------------- actions
    def authorize_action(self, p: Principal, action: str, *, order_region: str | None,
                         order_warehouse_code: str | None, target_warehouse_code: str | None = None) -> None:
        self.require(p, f"action.execute:{action}")
        regions = self.region_scope(p)
        if regions is not None and order_region not in regions:
            raise NotFound("commande introuvable ou hors périmètre")
        whs = self.warehouse_scope(p)
        if whs is not None:
            code = target_warehouse_code if action == "assign_warehouse" else order_warehouse_code
            if action == "assign_warehouse" and code is None:
                return  # évaluation sans entrepôt choisi : le périmètre est vérifié à l'exécution
            if code not in whs:
                raise Forbidden(f"entrepôt hors périmètre du principal ({code})", scope="warehouses")
