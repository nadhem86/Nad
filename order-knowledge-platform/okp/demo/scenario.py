"""Scénario de démonstration de bout en bout (6 étapes demandées).

L'« agent » de l'étape 4 est un enchaînement SCRIPTÉ et déterministe d'appels aux outils
agents (aucun LLM n'est appelé). Il montre le contrat que doit respecter un agent réel
connecté via MCP : n'utiliser que les outils, citer la provenance, signaler l'ambiguïté.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from okp.agent.tools import AgentToolbox
from okp.config import Settings
from okp.demo.generate import generate
from okp.operations.actions import ActionError
from okp.services import Platform


def _h(title: str) -> None:
    print("\n" + "=" * 88 + f"\n{title}\n" + "=" * 88)


def _prov(p: dict) -> str:
    if p["kind"] == "inferred":
        return f"[inféré {p['rule_id']} ← faits {p['derived_from'][:4]}{'…' if len(p['derived_from']) > 4 else ''}]"
    return f"[observé {p['source_system']}/{p['source_entity']}/{p['source_id']} · collecté {p['collected_at'][:19]} · {p['validation_status']}]"


def run_demo(settings: Settings, reset: bool = True) -> dict:
    transcript: dict = {}
    if reset:
        if settings.db_path.exists():
            for suffix in ("", "-wal", "-shm"):
                Path(str(settings.db_path) + suffix).unlink(missing_ok=True)
        if settings.sources_dir.exists():
            shutil.rmtree(settings.sources_dir)
        generate(settings.sources_dir)
    pf = Platform(settings)
    who = lambda pid, ch="api": pf.auth.principal(pid, ch)  # noqa: E731
    ingest, admin, manager = who("ingest.bot"), who("alice.admin"), who("bruno.manager")
    chloe, analyst = who("chloe.rep"), who("farid.analyst")

    # 1 ------------------------------------------------------------------------------------
    _h("1. Import du jeu de données fictif (ERP, CRM, WMS, SRM)")
    r1 = pf.run_ingestion(ingest, "full")
    print("Import complet  :", json.dumps(r1["stats"], ensure_ascii=False))
    for i in r1["issues"]:
        print(f"  quarantaine/revue → {i['source']} : {i['kind']} — {i['detail']}")
    r2 = pf.run_ingestion(ingest, "full")
    print("Réimport (idempotence) :", json.dumps(r2["stats"], ensure_ascii=False))
    val = pf.validate(admin)
    print(f"Validation SHACL : conforme={val['conforms']} ({val['triples']} triplets, {len(val['violations'])} violation(s))")
    rec = pf.reconcile(admin)
    print(f"Réconciliation core ↔ knowledge graph : cohérent={rec['consistent']}")
    transcript["import"] = {"first": r1["stats"], "second": r2["stats"], "shacl": val["conforms"], "reconcile": rec["consistent"]}

    # 2 ------------------------------------------------------------------------------------
    _h("2. Explorer les relations d'une commande (SO-10001, profil responsable ADV)")
    ov = pf.order_overview(manager, "SO-10001")
    o = ov["order"]
    print(f"{o['label']} ({o['canonical_id']}) — identifiants sources : "
          + ", ".join(f"{s['source_system']}:{s['source_id']}" for s in o["source_identifiers"]))
    for prop in ("status", "ordered_at", "order_date_local", "currency"):
        v = o["properties"][prop][0]
        print(f"  {prop:17} = {v['value']!s:24} {_prov(v['provenance'])}")
    print("Relations observées :")
    for r in ov["relations"]["observed"]:
        print(f"  {r['subject']['label']:24} —{r['predicate']}→ {r['object']['label']:30} {_prov(r['provenance'])}")
    print("Relations inférées :")
    for r in ov["relations"]["inferred"]:
        print(f"  {r['subject']['label']:24} —{r['predicate']}→ {r['object']['label']:30} {_prov(r['provenance'])}")
    transcript["order"] = {"id": o["canonical_id"], "observed": len(ov["relations"]["observed"]),
                           "inferred": len(ov["relations"]["inferred"])}

    # 3 ------------------------------------------------------------------------------------
    _h("3. Chiffre d'affaires et marge selon les définitions partagées (profil analyste)")
    for metric in ("revenue", "gross_margin", "gross_margin_rate"):
        m = pf.query_metric(analyst, metric)
        print(f"{m['label']:24} = {m['value']:,.2f} {m['unit']}  (modèle sémantique v{m['provenance']['semantic_model_version']},"
              f" données au {str(m['provenance']['data_as_of'])[:19]})")
        for w in m["warnings"]:
            print(f"    ⚠ {w['message']}")
    m = pf.query_metric(analyst, "revenue", ["time.month"], period={"from": "2026-01-01", "to": "2026-06-30"})
    print("CA par mois (date locale Europe/Paris) :", {r["time.month"]: round(r["value"], 2) for r in m["rows"]})
    try:
        pf.query_metric(chloe, "gross_margin")
    except Exception as exc:  # noqa: BLE001
        print(f"Commerciale IDF → marge : refusé ({exc})")
    rc = pf.query_metric(chloe, "revenue")
    print(f"Commerciale IDF → CA : {rc['value']:,.2f} EUR (filtre de sécurité appliqué côté serveur : {rc['security_filters']})")
    transcript["metrics"] = {n: pf.query_metric(analyst, n)["value"] for n in ("revenue", "gross_margin", "gross_margin_rate")}

    # 4 ------------------------------------------------------------------------------------
    _h("4. Question métier avec provenance (agent SCRIPTÉ via les outils, canal agent)")
    box = AgentToolbox(pf)
    agent_user = manager.on_channel("agent")
    q = "Si Nordic Components livre en retard, quelles commandes en cours et quels clients sont touchés ?"
    print(f"Question : {q}")
    t1 = box.call(agent_user, "resolve_entity", {"query": "Nordic Components", "concept": "Supplier"})
    print(f"  → resolve_entity : {t1['status']} {t1['data']['candidates']}")
    t2 = box.call(agent_user, "analyze_supplier_impact", {"supplier": t1["data"]["candidates"][0]["canonical_id"]})
    d = t2["data"]
    print("Réponse :")
    print(f"  Produits fournis (observé, SRM) : " + ", ".join(
        f"{p['label']} (délai {p['lead_time_days']} j, principal={p['is_primary']})" for p in d["products"]))
    print(f"  Commandes en cours dépendantes ({len(d['impacted_orders'])}, relation INFÉRÉE dependsOnSupplier) :")
    for io in d["impacted_orders"]:
        print(f"    - {io['erp_order_no']} [{io['status']}] client {io['customer']['label']} {_prov(io['provenance'])}")
    print(f"  Clients touchés : {', '.join(d['impacted_customers']) or 'aucun'}")
    print("  Indisponible : aucune date de livraison fournisseur n'est modélisée → l'ampleur du retard ne peut pas être estimée.")
    amb = box.call(agent_user, "compute_metric", {"metric": "ventes", "period_from": "2026-03-01", "period_to": "2026-03-31"})
    print(f"Question ambiguë « Quelles sont les ventes de mars ? » → {amb['status']} : "
          f"{[c['name'] for c in amb['data']['candidates']]} → l'agent doit demander une précision.")
    nd = box.call(agent_user, "compute_metric", {"metric": "revenue", "period_from": "2027-01-01"})
    print(f"Question sans données « CA 2027 ? » → {nd['status']} (value={nd['data']['value']}) → « information indisponible », pas 0.")
    c3 = box.call(agent_user, "resolve_entity", {"query": "C003"})
    e3 = box.call(agent_user, "get_entity", {"entity_id": c3["data"]["candidates"][0]["canonical_id"]})
    note = e3["data"]["properties"]["note"][0]["value"]
    print(f"Texte source non fiable (C003) renvoyé comme donnée encapsulée : untrusted_text=« {note['untrusted_text'][:60]}… »")
    print(f"  Champs masqués pour l'agent (minimisation PII) : {e3['data']['masked_properties']}")
    transcript["agent"] = {"impacted_orders": [i["erp_order_no"] for i in d["impacted_orders"]], "ambiguous": amb["status"],
                           "no_data": nd["status"]}

    # 5 ------------------------------------------------------------------------------------
    _h("5. Action autorisée : la commerciale IDF confirme SO-20001")
    before = pf.available_actions(chloe, "SO-20001")
    print("Actions disponibles :", [(a["action"], a["executable"]) for a in before["actions"]])
    res = pf.execute_action(chloe, "confirm_order", {"order_id": "SO-20001", "idempotency_key": "demo-confirm-SO-20001"})
    print(f"Résultat : {res['previous_status']} → {res['new_status']} ; autorité={res['authority']['system']} "
          f"(reçu ERP row_version={res['authority']['receipt']['row_version']}) ; synchro={res['sync_status']} ; audit #{res['audit_id']}")
    again = pf.execute_action(chloe, "confirm_order", {"order_id": "SO-20001", "idempotency_key": "demo-confirm-SO-20001"})
    print(f"Rejeu avec la même clé : replayed={again['replayed']} (aucun nouvel effet)")
    hist = pf.order_overview(chloe, "SO-20001")["status_history"]
    print("Historique de statut (faits, valid_from → valid_to) :",
          [(h["status"], h["provenance"]["valid_from"], h["provenance"]["valid_to"]) for h in hist])
    transcript["action"] = {"new_status": res["new_status"], "replayed": again["replayed"]}

    # 6 ------------------------------------------------------------------------------------
    _h("6. Même action refusée pour un rôle non autorisé")
    for principal, order in ((analyst, "SO-20002"), (chloe, "SO-20002")):
        try:
            pf.execute_action(principal, "confirm_order", {"order_id": order, "idempotency_key": f"deny-{principal.id}-{order}"})
        except ActionError as exc:
            print(f"{principal.id:14} confirm_order {order} → {exc.code} : {exc.message}")
    denied = box.call(agent_user, "execute_action", {"action": "cancel_order", "order_id": "SO-20002",
                                                     "reason": "test agent", "idempotency_key": "agent-cancel-0001"})
    print(f"bruno.manager via canal agent → cancel_order : {denied['status']} ({denied['error']['message']})")
    audit = pf.audit_events(admin, 5)
    print(f"Journal d'audit : intégrité={audit['integrity']}")
    for e in audit["events"]:
        print(f"  #{e['seq']} {e['principal_id']:14} {e['channel']:5} {e['event_type']:22} {e['decision']}")
    transcript["audit_valid"] = audit["integrity"]["valid"]
    return transcript
