"""Chargement et validation des modèles métier versionnés (models/*.yaml).

Le registre est l'unique point d'accès aux définitions pour tout le code : ingestion,
knowledge graph, moteur de métriques, actions, politique d'accès et outils agents.
"""

from __future__ import annotations

import hashlib
from functools import cached_property
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

PropType = Literal["string", "integer", "decimal", "boolean", "date", "datetime", "enum"]
Sensitivity = Literal["public", "internal", "confidential", "pii"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PropertyDef(_Model):
    type: PropType
    required: bool = False
    sensitivity: Sensitivity = "internal"
    definition: str
    values: list[str] | None = None
    pattern: str | None = None
    min: float | None = None
    max: float | None = None
    free_text: bool = False
    derived: bool = False

    @model_validator(mode="after")
    def _enum_values(self):
        if self.type == "enum" and not self.values:
            raise ValueError("une propriété enum doit lister ses valeurs")
        return self


class Ambiguity(_Model):
    service: str
    meaning: str
    resolution: str


class MatchRule(_Model):
    id: str
    description: str
    confidence: float
    auto_merge: bool


class Identity(_Model):
    anchor: dict[str, str]
    match_rules: list[MatchRule] = []


class SourceMapping(_Model):
    system: str
    entity: str
    key: str
    authority_for: list[str] = []
    match: str | None = None
    note: str | None = None


class ConceptDef(_Model):
    label: str
    table: str
    id_prefix: str
    owner: str
    definition: str
    synonyms: list[str] = []
    ambiguities: list[Ambiguity] = []
    identity: Identity
    source_mappings: list[SourceMapping]
    properties: dict[str, PropertyDef]


class RelationDef(_Model):
    label: str
    domain: str
    range: str
    min: int = 0
    max: int | None = None
    inverse_label: str | None = None
    inverse_min: int = 0
    inverse_max: int | None = None
    definition: str
    qualifiers: dict[str, dict[str, str]] = {}
    projection: dict[str, Any]


class InferredRelationDef(_Model):
    label: str
    domain: str
    range: str
    rule_id: str
    rule: str


class ConstraintDef(_Model):
    id: str
    description: str
    enforced_in: list[str]
    severity: Literal["violation", "warning"] = "violation"
    target: str = "Order"
    sparql: str | None = None


class Glossary(_Model):
    version: str
    namespace: str
    instance_namespace: str
    sensitivity_levels: dict[str, str]
    concepts: dict[str, ConceptDef]
    relations: dict[str, RelationDef]
    inferred_relations: dict[str, InferredRelationDef]
    constraints: list[ConstraintDef]

    @model_validator(mode="after")
    def _references(self):
        for name, rel in {**self.relations, **self.inferred_relations}.items():
            if rel.domain not in self.concepts:
                raise ValueError(f"relation {name}: domaine inconnu {rel.domain}")
            if rel.range != "literal" and rel.range not in self.concepts:
                raise ValueError(f"relation {name}: portée inconnue {rel.range}")
        for c in self.constraints:
            if c.target not in self.concepts:
                raise ValueError(f"contrainte {c.id}: cible inconnue {c.target}")
        prefixes = [c.id_prefix for c in self.concepts.values()]
        if len(set(prefixes)) != len(prefixes):
            raise ValueError("préfixes d'identifiants canoniques dupliqués")
        return self

    def predicate(self, concept: str, prop: str) -> str:
        """Identifiant de prédicat d'attribut, partagé par le KG, l'export RDF et le SHACL."""
        return f"{concept.lower()}.{prop}"

    def concept_by_prefix(self, canonical_id: str) -> str | None:
        prefix = canonical_id.split("-", 1)[0]
        for name, c in self.concepts.items():
            if c.id_prefix == prefix:
                return name
        return None


class DimensionDef(_Model):
    label: str
    concept: str | None = None
    table: str | None = None
    key: str | None = None
    attributes: dict[str, dict[str, str]] = {}
    degenerate: bool = False
    column: str | None = None
    values: list[str] | None = None
    grains: dict[str, str] | None = None


class JoinDef(_Model):
    column: str
    cardinality: Literal["many_to_one"]
    optional: bool = False


class FactDef(_Model):
    label: str
    view: str
    sources: list[str]
    grain: str
    grain_keys: list[str]
    time_column: str
    joins: dict[str, JoinDef]
    degenerate: list[str]


class QualityCheck(_Model):
    id: str
    message: str
    count_filter: str


class MetricDef(_Model):
    label: str
    synonyms: list[str] = []
    owner: str
    description: str
    fact: str
    type: Literal["sum", "count_distinct", "ratio", "last_snapshot_sum"]
    expression: str | None = None
    numerator: str | None = None
    denominator: str | None = None
    filters: list[str] = []
    quality_checks: list[QualityCheck] = []
    unit: str
    additivity: Literal["additive", "semi_additive", "non_additive"]
    additivity_note: str | None = None
    sensitivity: Sensitivity = "internal"
    rules: dict[str, str] = {}

    @model_validator(mode="after")
    def _shape(self):
        if self.type == "ratio" and not (self.numerator and self.denominator):
            raise ValueError("une métrique ratio exige numerator et denominator")
        if self.type != "ratio" and not self.expression:
            raise ValueError("expression requise")
        return self


class SemanticModel(_Model):
    version: str
    reporting_currency: str
    timezone: str
    conventions: dict[str, str]
    dimensions: dict[str, DimensionDef]
    facts: dict[str, FactDef]
    metrics: dict[str, MetricDef]

    @model_validator(mode="after")
    def _references(self):
        for fname, fact in self.facts.items():
            for d in list(fact.joins) + fact.degenerate:
                if d not in self.dimensions:
                    raise ValueError(f"fait {fname}: dimension inconnue {d}")
        for mname, m in self.metrics.items():
            if m.fact not in self.facts:
                raise ValueError(f"métrique {mname}: fait inconnu {m.fact}")
        return self


class ActionDef(_Model):
    label: str
    permission: str
    input: dict[str, dict[str, Any]]
    output: str
    preconditions: list[str]
    effects: list[str]
    errors: list[str]
    idempotency: str
    audit: str


class OperationsModel(_Model):
    version: str
    entity: str
    authority: str
    states: dict[str, dict[str, Any]]
    transitions: list[dict[str, str]]
    actions: dict[str, ActionDef]
    error_codes: dict[str, str]

    @model_validator(mode="after")
    def _references(self):
        for t in self.transitions:
            if t["from"] not in self.states or t["to"] not in self.states:
                raise ValueError(f"transition invalide {t}")
            if t["action"] not in self.actions:
                raise ValueError(f"transition vers action inconnue {t}")
        for a in self.actions.values():
            for e in a.errors:
                if e not in self.error_codes:
                    raise ValueError(f"code d'erreur inconnu {e}")
        return self

    def target_state(self, action: str, from_state: str) -> str | None:
        for t in self.transitions:
            if t["action"] == action and t["from"] == from_state:
                return t["to"]
        return None

    def erp_code(self, state: str) -> str:
        return self.states[state]["erp_code"]

    def state_from_erp(self, code: str) -> str:
        for name, s in self.states.items():
            if s["erp_code"] == code:
                return name
        raise KeyError(code)


class RoleDef(_Model):
    description: str
    permissions: list[str]
    scope_attributes: list[str] = []


class PolicyModel(_Model):
    version: str
    roles: dict[str, RoleDef]
    channels: dict[str, dict[str, Any]]


class Registry:
    """Ensemble cohérent et versionné des modèles métier."""

    def __init__(self, models_dir: Path):
        self.models_dir = Path(models_dir)
        self.glossary = Glossary(**self._load("glossary.yaml"))
        self.semantic = SemanticModel(**self._load("semantic_model.yaml"))
        self.operations = OperationsModel(**self._load("operations.yaml"))
        self.policies = PolicyModel(**self._load("policies.yaml"))
        self._check_cross_model()

    def _load(self, name: str) -> dict:
        with open(self.models_dir / name, encoding="utf-8") as fh:
            return yaml.safe_load(fh)

    def _check_cross_model(self) -> None:
        for dname, d in self.semantic.dimensions.items():
            if d.concept and d.concept not in self.glossary.concepts:
                raise ValueError(f"dimension {dname}: concept inconnu {d.concept}")
        if self.operations.entity not in self.glossary.concepts:
            raise ValueError("operations.entity inconnu du glossaire")
        order_status = self.glossary.concepts[self.operations.entity].properties["status"].values
        if set(order_status or []) != set(self.operations.states):
            raise ValueError("les statuts du glossaire et de la machine à états divergent")

    @cached_property
    def versions(self) -> dict[str, str]:
        return {
            "glossary": self.glossary.version,
            "semantic_model": self.semantic.version,
            "operations": self.operations.version,
            "policies": self.policies.version,
        }

    @cached_property
    def fingerprint(self) -> str:
        h = hashlib.sha256()
        for name in ("glossary.yaml", "semantic_model.yaml", "operations.yaml", "policies.yaml"):
            h.update((self.models_dir / name).read_bytes())
        return h.hexdigest()[:16]
