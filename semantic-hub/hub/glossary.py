"""Chargement du glossaire (source de vérité des définitions) et accès typé aux concepts."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .config import Settings


@dataclass(frozen=True)
class Property:
    concept: str
    name: str
    type: str
    required: bool = False
    pii: bool = False
    untrusted: bool = False
    inferred_only: bool = False
    values: tuple[str, ...] = ()
    definition: str = ""
    constraints: dict[str, Any] = field(default_factory=dict)

    @property
    def is_relation(self) -> bool:
        return False


@dataclass(frozen=True)
class Relation:
    concept: str
    name: str
    target: str
    min: int
    max: int | None  # None = illimité
    inferred_only: bool = False
    definition: str = ""

    @property
    def is_relation(self) -> bool:
        return True

    @property
    def multi(self) -> bool:
        return self.max is None or self.max > 1


@dataclass(frozen=True)
class Concept:
    name: str
    slug: str
    label: str
    definition: str
    owner: str
    synonyms: tuple[str, ...]
    ambiguities: tuple[dict, ...]
    source_ids: tuple[dict, ...]
    properties: dict[str, Property]
    relations: dict[str, Relation]

    def member(self, name: str) -> Property | Relation | None:
        return self.properties.get(name) or self.relations.get(name)


class Glossary:
    def __init__(self, raw: dict):
        self.raw = raw
        self.version: str = raw["version"]
        self.namespace: str = raw["namespace"]
        self.concepts: dict[str, Concept] = {}
        for cname, c in raw["concepts"].items():
            props = {}
            for pname, p in (c.get("properties") or {}).items():
                constraints = {k: p[k] for k in ("pattern", "min_inclusive", "min_exclusive", "max_inclusive") if k in p}
                props[pname] = Property(
                    concept=cname, name=pname, type=p["type"], required=p.get("required", False),
                    pii=p.get("pii", False), untrusted=p.get("untrusted", False),
                    inferred_only=p.get("inferred_only", False), values=tuple(p.get("values", ())),
                    definition=p.get("definition", ""), constraints=constraints,
                )
            rels = {}
            for rname, r in (c.get("relations") or {}).items():
                mx = r.get("max", 1)
                rels[rname] = Relation(
                    concept=cname, name=rname, target=r["target"], min=r.get("min", 0),
                    max=None if mx == "*" else int(mx), inferred_only=r.get("inferred_only", False),
                    definition=r.get("definition", ""),
                )
            self.concepts[cname] = Concept(
                name=cname, slug=c["slug"], label=c["label"], definition=" ".join(c["definition"].split()),
                owner=c["owner"], synonyms=tuple(c.get("synonyms", ())), ambiguities=tuple(c.get("ambiguities", ())),
                source_ids=tuple(c.get("source_ids", ())), properties=props, relations=rels,
            )
        self.impact_paths: dict[str, list[dict]] = raw.get("impact_paths", {})
        self.ambiguous_terms: dict[str, list[str]] = raw.get("ambiguous_terms", {})
        self._by_slug = {c.slug: c for c in self.concepts.values()}

    # -- accès -------------------------------------------------------------------------------------
    def concept(self, name: str) -> Concept:
        return self.concepts[name]

    def concept_by_slug(self, slug: str) -> Concept:
        return self._by_slug[slug]

    def member(self, concept: str, name: str) -> Property | Relation:
        m = self.concepts[concept].member(name)
        if m is None:
            raise KeyError(f"{concept}.{name} absent du glossaire v{self.version}")
        return m

    def predicates(self) -> set[str]:
        out: set[str] = set()
        for c in self.concepts.values():
            out |= set(c.properties) | set(c.relations)
        return out

    def relation_names(self) -> set[str]:
        return {r for c in self.concepts.values() for r in c.relations}

    def pii_predicates(self, concept: str) -> set[str]:
        return {p.name for p in self.concepts[concept].properties.values() if p.pii}

    def untrusted_predicates(self, concept: str) -> set[str]:
        return {p.name for p in self.concepts[concept].properties.values() if p.untrusted}

    def iri(self, local: str) -> str:
        return self.namespace + local


@lru_cache(maxsize=4)
def _load(path: str) -> Glossary:
    return Glossary(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def load_glossary(settings: Settings | None = None) -> Glossary:
    return _load(str((settings or Settings()).glossary_path))
