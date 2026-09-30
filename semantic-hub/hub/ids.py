"""Identifiants canoniques et normalisation des clés de résolution d'identité."""

from __future__ import annotations

import re
import uuid

# Espace de noms fixe : les IRI sont déterministes (même source + même identifiant => même IRI),
# ce qui rend l'ingestion reproductible et idempotente.
NAD_NAMESPACE = uuid.UUID("6f1c2d8e-2b7a-4c55-9f0e-1a2b3c4d5e6f")

_COUNTRY_CODES = {"FR": "33", "BE": "32", "CH": "41", "TN": "216", "MA": "212"}


def canonical_iri(slug: str, *key_parts: str) -> str:
    key = "|".join(key_parts)
    return f"urn:nad:{slug}:{uuid.uuid5(NAD_NAMESPACE, slug + '|' + key).hex[:12]}"


def normalize_phone(raw: str | None, default_region: str = "FR") -> str | None:
    """Normalise un numéro en E.164 (+33612345678). Retourne None si le numéro est inexploitable.

    Règles volontairement simples (pas de dépendance à libphonenumber) :
    « 06 12 34 56 78 » -> +33612345678 ; « 0033 6 … » -> +336… ; « +33 6 … » -> +336….
    """
    if not raw:
        return None
    s = raw.strip()
    plus = s.startswith("+")
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None
    if plus:
        e164 = "+" + digits
    elif digits.startswith("00"):
        e164 = "+" + digits[2:]
    elif digits.startswith("0") and default_region in _COUNTRY_CODES:
        e164 = "+" + _COUNTRY_CODES[default_region] + digits[1:]
    else:
        e164 = "+" + digits
    return e164 if re.fullmatch(r"\+[1-9]\d{6,14}", e164) else None


def normalize_text(s: str | None) -> str:
    import unicodedata

    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
