"""Contrôle d'accès RBAC + ABAC, appliqué côté serveur pour toutes les interfaces.

Le principal est résolu à partir d'un jeton (empreinte SHA-256) : un agent IA ne peut ni choisir
ni élargir ses droits par le contenu d'un prompt.
"""

from __future__ import annotations

import fnmatch
import hashlib
import hmac
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .kg import KnowledgeGraph


@dataclass(frozen=True)
class Principal:
    id: str
    roles: tuple[str, ...]
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def is_agent(self) -> bool:
        return bool(self.attributes.get("is_agent"))


class AccessDenied(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code  # FORBIDDEN | OUT_OF_SCOPE | UNAUTHENTICATED


class PolicyEngine:
    def __init__(self, policies_path: Path, principals_path: Path | str):
        pol = yaml.safe_load(Path(policies_path).read_text(encoding="utf-8"))
        self.version = pol["version"]
        self.roles = pol["roles"]
        raw = yaml.safe_load(Path(principals_path).read_text(encoding="utf-8"))
        self._principals = raw.get("principals", [])

    # ---------------------------------------------------------------- authentification
    def authenticate(self, token: str | None) -> Principal:
        if not token:
            raise AccessDenied("UNAUTHENTICATED", "jeton absent")
        digest = hashlib.sha256(token.encode()).hexdigest()
        for p in self._principals:
            if hmac.compare_digest(digest, str(p["token_sha256"])):
                return Principal(p["id"], tuple(p["roles"]), dict(p.get("attributes") or {}))
        raise AccessDenied("UNAUTHENTICATED", "jeton inconnu")

    def principal(self, pid: str) -> Principal:
        for p in self._principals:
            if p["id"] == pid:
                return Principal(p["id"], tuple(p["roles"]), dict(p.get("attributes") or {}))
        raise KeyError(pid)

    # ---------------------------------------------------------------- permissions
    def permissions(self, principal: Principal) -> set[str]:
        out: set[str] = set()

        def add(role: str) -> None:
            r = self.roles[role]
            out.update(r.get("permissions", []))
            for parent in r.get("inherits", []):
                add(parent)

        for role in principal.roles:
            add(role)
        return out

    def has(self, principal: Principal, perm: str) -> bool:
        return any(p == "*" or fnmatch.fnmatchcase(perm, p) for p in self.permissions(principal))

    def require(self, principal: Principal, perm: str) -> None:
        if not self.has(principal, perm):
            raise AccessDenied("FORBIDDEN", f"permission requise : {perm}")

    # ---------------------------------------------------------------- périmètres (ABAC)
    @staticmethod
    def _in(principal: Principal, attribute: str, value: str | None) -> bool:
        allowed = principal.attributes.get(attribute)
        if allowed is None:
            return False
        return "*" in allowed or (value is not None and value in allowed)

    def region_ok(self, principal: Principal, region: str | None) -> bool:
        return self._in(principal, "regions", region)

    def warehouse_ok(self, principal: Principal, code: str | None) -> bool:
        return self._in(principal, "entrepots", code)

    def client_region(self, kg: KnowledgeGraph, iri: str) -> str | None:
        """Région du client dont dépend une entité (Client, Contact, Commande, Ligne, IdentiteWhatsApp)."""
        concept = kg.concept_of(iri)
        if concept == "Client":
            return kg.get(iri, "region")
        if concept == "Contact":
            c = kg.get(iri, "travaillePour")
            return kg.get(c, "region") if c else None
        if concept == "Commande":
            c = kg.get(iri, "passeePar")
            return kg.get(c, "region") if c else None
        if concept == "LigneCommande":
            o = kg.get(iri, "ligneDe")
            return self.client_region(kg, o) if o else None
        if concept == "IdentiteWhatsApp":
            c = kg.get(iri, "identifie")  # uniquement un lien VALIDÉ
            return self.client_region(kg, c) if c else None
        return None

    REGION_SCOPED = ("Client", "Contact", "Commande", "LigneCommande", "IdentiteWhatsApp")

    def can_read(self, principal: Principal, kg: KnowledgeGraph, iri: str) -> tuple[bool, str | None]:
        concept = kg.concept_of(iri)
        if concept is None:
            return False, "NOT_FOUND"
        if not self.has(principal, f"entity:read:{concept}"):
            return False, "FORBIDDEN"
        if concept in self.REGION_SCOPED and not self.region_ok(principal, self.client_region(kg, iri)):
            return False, "OUT_OF_SCOPE"
        return True, None

    def can_read_pii(self, principal: Principal) -> bool:
        return self.has(principal, "pii:read")

    def region_filter(self, principal: Principal) -> list[str] | None:
        """None = aucune restriction ; sinon liste des régions autorisées pour les métriques."""
        regions = principal.attributes.get("regions")
        if regions is None:
            return []
        return None if "*" in regions else list(regions)


def mask(value: Any) -> str:
    s = str(value)
    return "***" if len(s) <= 4 else s[:2] + "*" * (len(s) - 4) + s[-2:]


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
