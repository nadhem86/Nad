"""Ingestion de l'export JSON produit par l'extension whatsapp-extractor (popup.js > exportJSON).

Minimisation des données (RGPD) : seuls le numéro, le nom affiché, le drapeau Business et
l'appartenance aux groupes sont conservés. Les messages, statuts « à propos », compteurs de non-lus
et conversations sont IGNORÉS.
Un export est PARTIEL (seules les discussions chargées dans WhatsApp Web y figurent) : l'absence
d'un numéro ne signifie pas sa suppression. Aucun retrait n'est donc déduit d'un export.
Les textes (noms, description de groupe) sont des données NON FIABLES, jamais des instructions.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from ..db import utcnow
from ..ids import normalize_phone
from ..kg import IRI, KnowledgeGraph

SOURCE = "whatsapp"
DROPPED_FIELDS = ("lastMessage", "about", "unreadCount", "isMuted", "isPinned", "isArchived", "lastSeen", "lastActivity")
# Priorité des méthodes d'extraction de l'extension pour choisir le nom affiché.
SOURCE_PRIORITY = {"Store.Contact": 0, "Store.Chat.private": 1, "Store.Group.participant": 2}


class WhatsAppIngestor:
    def __init__(self, kg: KnowledgeGraph, run_id: str, phone_region: str = "FR"):
        self.kg = kg
        self.run_id = run_id
        self.phone_region = phone_region
        self.stats: Counter = Counter()
        self.rejected: list[dict] = []

    def ingest_export(self, data: dict) -> dict:
        collected = data.get("exportedAt") or utcnow()
        identities: dict[str, dict] = {}
        for c in data.get("contacts", []):
            e164 = normalize_phone(c.get("phone"), self.phone_region)
            if not e164:
                self.rejected.append({"phone": c.get("phone"), "reason": "numéro non normalisable"})
                continue
            self.stats["records_contacts"] += 1
            self.stats["champs_ignores_minimisation"] += sum(1 for k in DROPPED_FIELDS if k in c)
            prio = SOURCE_PRIORITY.get(c.get("source", ""), 9)
            cur = identities.get(e164)
            if cur is None:
                identities[e164] = {"name": c.get("name") or None, "prio": prio, "business": bool(c.get("isBusiness"))}
            else:
                self.stats["doublons_fusionnes"] += 1
                cur["business"] = cur["business"] or bool(c.get("isBusiness"))
                if prio < cur["prio"] and c.get("name"):
                    cur.update(name=c["name"], prio=prio)

        membership: dict[str, set[str]] = defaultdict(set)
        for g in data.get("groups", []):
            gid = g.get("id") or g.get("name")
            giri = self.kg.ensure_entity("GroupeWhatsApp", SOURCE, "groups", gid, label=g.get("name"))
            s = self.kg.upsert_facts(giri, {"nomGroupe": g.get("name") or gid, "description": g.get("description") or None},
                                     source=SOURCE, source_record_id=f"groups/{gid}", collected_at=collected,
                                     valid_from=collected, run_id=self.run_id)
            self._count(s)
            self.stats["champs_ignores_minimisation"] += sum(1 for k in DROPPED_FIELDS if k in g)
            for p in g.get("participants", []):
                e164 = normalize_phone(p.get("phone"), self.phone_region)
                if e164:
                    membership[e164].add(giri)
                    identities.setdefault(e164, {"name": p.get("name") or None, "prio": 2, "business": False})

        self.stats["conversations_ignorees"] += len(data.get("conversations", []))
        for e164, info in identities.items():
            iri = self.kg.ensure_entity("IdentiteWhatsApp", SOURCE, "phone", e164, label=e164)
            s = self.kg.upsert_facts(
                iri,
                {"numeroE164": e164, "nomAffiche": info["name"], "estBusiness": info["business"],
                 "membreDe": [IRI(g) for g in sorted(membership.get(e164, ()))]},
                source=SOURCE, source_record_id=f"contacts/{e164}", collected_at=collected, valid_from=collected,
                run_id=self.run_id,
            )
            self._count(s)
        self.stats["identites"] = len(identities)
        return {"stats": dict(self.stats), "rejected": self.rejected}

    def _count(self, s: Counter) -> None:
        self.stats.update({f"facts_{k}": v for k, v in s.items()})
