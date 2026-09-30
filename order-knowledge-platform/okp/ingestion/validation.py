"""Validation d'un enregistrement projeté contre les définitions du glossaire."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from okp.registry import ConceptDef, PropertyDef


def validate_props(concept: ConceptDef, props: dict[str, Any], *, anchor: bool) -> list[str]:
    errors: list[str] = []
    for name, value in props.items():
        pdef = concept.properties.get(name)
        if pdef is None:
            errors.append(f"propriété inconnue du glossaire : {name}")
            continue
        if value is None:
            if pdef.required and anchor:
                errors.append(f"{name} : obligatoire")
            continue
        errors.extend(f"{name} : {e}" for e in _check(pdef, value))
    if anchor:
        for name, pdef in concept.properties.items():
            if pdef.required and name not in props:
                errors.append(f"{name} : obligatoire (absent de la source)")
    return errors


def _check(p: PropertyDef, v: Any) -> list[str]:
    errs = []
    if p.type in ("string", "enum"):
        if not isinstance(v, str):
            return [f"chaîne attendue, reçu {type(v).__name__}"]
        if p.type == "enum" and v not in (p.values or []):
            errs.append(f"valeur {v!r} hors énumération {p.values}")
        if p.pattern and not re.search(p.pattern, v):
            errs.append(f"valeur {v!r} ne respecte pas le motif {p.pattern}")
    elif p.type == "integer":
        if isinstance(v, bool) or not isinstance(v, int):
            return [f"entier attendu, reçu {v!r}"]
    elif p.type == "decimal":
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return [f"nombre attendu, reçu {v!r}"]
    elif p.type == "boolean":
        if not isinstance(v, bool):
            return [f"booléen attendu, reçu {v!r}"]
    elif p.type == "date":
        try:
            date.fromisoformat(v)
        except (TypeError, ValueError):
            return [f"date ISO attendue, reçu {v!r}"]
    elif p.type == "datetime":
        try:
            datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except ValueError:
            return [f"horodatage ISO attendu, reçu {v!r}"]
    if p.type in ("integer", "decimal"):
        if p.min is not None and v < p.min:
            errs.append(f"{v} < minimum {p.min}")
        if p.max is not None and v > p.max:
            errs.append(f"{v} > maximum {p.max}")
    return errs
