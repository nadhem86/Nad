"""Règles d'inférence déterministes. Chaque fait produit est marqué method='inference', porte l'identifiant
de sa règle et la liste des faits-prémisses (derived_from). Il est donc identifiable et traçable.
Les règles sont rejouées intégralement après chaque ingestion ou action (dérivation idempotente).

R3 : statut consolidé d'une Commande (statutCRM de vTiger + statutLogistique du WMS).
R1 : IdentiteWhatsApp identifie Contact si le numéro E.164 correspond à UN SEUL contact.
     Si plusieurs contacts partagent le numéro, les liens sont créés avec status='a_verifier' (ambiguïté).
R2 : Client dependDe Fournisseur si une commande non annulée du client porte sur un produit du fournisseur.
"""

from __future__ import annotations

from collections import defaultdict

from .kg import KnowledgeGraph

RULES = {
    "R1": "Identité WhatsApp -> Contact par numéro E.164 identique (unique : valide ; multiple : a_verifier)",
    "R2": "Client dependDe Fournisseur via Commande (hors brouillon/annulée) -> Ligne -> Produit -> fourniPar",
    "R3": "Statut consolidé = f(statutCRM, statutLogistique) ; priorité : annulee > livree > expediee > en_preparation > CRM",
}


def consolidate_status(crm: str | None, logistique: str | None) -> str | None:
    if crm is None:
        return None
    if crm == "annulee":
        return "annulee"
    if crm == "livree" or logistique == "livree":
        return "livree"
    if logistique in ("expediee", "en_preparation"):
        return logistique
    return crm


def rule_r3(kg: KnowledgeGraph) -> list[dict]:
    out = []
    for order in kg.entities("Commande"):
        crm = kg.fact_for(order, "statutCRM")
        log = kg.fact_for(order, "statutLogistique")
        statut = consolidate_status(crm["object_value"] if crm else None, log["object_value"] if log else None)
        if statut is None:
            continue
        out.append({"subject": order, "predicate": "statut", "object": statut,
                    "derived_from": [f["fact_id"] for f in (crm, log) if f is not None]})
    return out


def rule_r1(kg: KnowledgeGraph) -> list[dict]:
    phones: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for contact in kg.entities("Contact"):
        for pred in ("mobile", "telephone"):
            f = kg.fact_for(contact, pred)
            if f is not None:
                phones[f["object_value"]].append((contact, f["fact_id"]))
    out = []
    for waid in kg.entities("IdentiteWhatsApp"):
        f = kg.fact_for(waid, "numeroE164")
        if f is None:
            continue
        candidates: dict[str, int] = {}
        for contact, fid in phones.get(f["object_value"], []):
            candidates.setdefault(contact, fid)
        unique = len(candidates) == 1
        for contact, fid in candidates.items():
            out.append({"subject": waid, "predicate": "identifie", "object": contact,
                        "derived_from": [f["fact_id"], fid],
                        "status": "valide" if unique else "a_verifier",
                        "confidence": 1.0 if unique else round(1 / len(candidates), 2)})
    return out


def rule_r2(kg: KnowledgeGraph) -> list[dict]:
    deps: dict[tuple[str, str], set[int]] = defaultdict(set)
    for order in kg.entities("Commande"):
        st = kg.fact_for(order, "statut")
        if st is None or st["object_value"] in ("brouillon", "annulee"):
            continue
        client = kg.fact_for(order, "passeePar")
        if client is None:
            continue
        for line, trail in kg.traverse(order, ["^ligneDe"]):
            prod = kg.fact_for(line, "porteSur")
            if prod is None:
                continue
            for vf in kg.facts(prod["object_iri"], "fourniPar", statuses=("valide",)):
                deps[(client["object_iri"], vf["object_iri"])] |= {st["fact_id"], client["fact_id"], *trail,
                                                                    prod["fact_id"], vf["fact_id"]}
    return [{"subject": c, "predicate": "dependDe", "object": v, "derived_from": sorted(ids)}
            for (c, v), ids in sorted(deps.items())]


def run_all(kg: KnowledgeGraph, run_id: str | None) -> dict:
    # R3 d'abord : R2 dépend du statut consolidé.
    stats = {}
    for rule_id, fn in (("R3", rule_r3), ("R1", rule_r1), ("R2", rule_r2)):
        stats[rule_id] = dict(kg.replace_inferred(rule_id, fn(kg), run_id))
    return stats
