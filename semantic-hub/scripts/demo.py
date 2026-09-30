"""Démonstration de bout en bout, reproductible, sur une base neuve (var/demo.sqlite).

1. Import du jeu fictif (vTiger, WMS, WhatsApp, taux de change) et validation SHACL
2. Exploration des relations d'une commande
3. CA et marge selon les définitions partagées
4. Réponse à une question métier avec provenance (agent SCRIPTÉ : séquence d'appels d'outils, sans LLM)
5. Action autorisée
6. Même action refusée pour un rôle non autorisé
(+) propagation d'une modification source (delta vTiger), idempotence et audit
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("NAD_DB_PATH", str(ROOT / "var" / "demo.sqlite"))
db = Path(os.environ["NAD_DB_PATH"])
for suffix in ("", "-wal", "-shm"):
    Path(str(db) + suffix).unlink(missing_ok=True)

from hub.ingest.pipeline import ingest, load_demo  # noqa: E402
from hub.rdf import validate  # noqa: E402
from hub.services import Hub  # noqa: E402


def title(t: str) -> None:
    print("\n" + "=" * 100 + f"\n{t}\n" + "=" * 100)


def show(obj, limit: int = 1800) -> None:
    s = json.dumps(obj, ensure_ascii=False, indent=1, default=str)
    print(s if len(s) <= limit else s[:limit] + f"\n… ({len(s) - limit} caractères tronqués)")


hub = Hub()
P = hub.policy.principal
direction = hub.toolkit(P("alice.admin"))
bruno = hub.toolkit(P("bruno.commercial.nord"))
david = hub.toolkit(P("david.logisticien.lille"))
agent = hub.toolkit(P("agent.commercial.nord"))
analyste = hub.toolkit(P("emma.analyste"))

title("1. IMPORT DU JEU DE DONNÉES FICTIF")
for r in load_demo(hub):
    print(f"- {r['run_id']:<22} mode={r['mode']:<9} stats={r['stats']} rejets={len(r['rejected'])} inférences={r['inference']}")
again = ingest(hub, "vtiger", hub.settings.demo_dir / "vtiger" / "snapshot_2026-09-30.json")
print(f"- ré-import du même snapshot vTiger (idempotence) : {again['stats']}")
ok, violations = validate(hub.kg)
print(f"- validation SHACL : conforme={ok}, violations={len(violations)}")

title("2. EXPLORER LES RELATIONS D'UNE COMMANDE (SO102)")
so102 = direction.resolve_entity("SO102")["data"]["entity"]["iri"]
rel = direction.relations(so102, depth=2)
stock_edges = [e for e in rel["data"]["edges"] if e["predicate"] in ("nad:stockDans", "nad:stockDe")]
for e in rel["data"]["edges"]:
    if e in stock_edges:
        continue
    tag = f"INFÉRÉ {e['provenance'].get('rule_id')}" if e["inferred"] else f"{e['provenance']['source']}:{e['provenance']['source_record_id']}"
    print(f"  {e['subject_label']:<28} --{e['predicate']:<22}--> {e['object_label']:<32} [{tag}]")
print(f"  (+ {len(stock_edges)} relevés de stock liés à l'entrepôt, non affichés)")
ent = direction.get_entity(so102)["data"]
print("  statut consolidé (inféré R3) :", [f["value"] for f in ent["inferred"] if f["predicate"] == "nad:statut"],
      "prémisses", [f["provenance"]["derived_from"] for f in ent["inferred"] if f["predicate"] == "nad:statut"])

title("3. CHIFFRE D'AFFAIRES ET MARGE (T3 2026, définitions partagées)")
res = direction.compute_metric(["chiffre_affaires", "marge", "taux_marge", "nb_commandes"], group_by=["client.nom"],
                               period_start="2026-07-01", period_end="2026-09-30")
for m, r in res["data"].items():
    print(f"  {m} ({r['definition']['unit']}, {r['definition']['additivity']}) :")
    for row in r["rows"]:
        print(f"     {row['client.nom']:<30} {row['value']}")
print("  avertissements :")
for w in res.get("warnings", []):
    print("    -", w)
tot = direction.compute_metric(["chiffre_affaires", "marge", "taux_marge", "panier_moyen", "delai_traitement",
                                "taux_annulation", "quantite_vendue"], period_start="2026-07-01", period_end="2026-09-30")
print("  totaux T3 :", {m: r["rows"][0]["value"] if r["rows"] else None for m, r in tot["data"].items()})
fan = direction.compute_metric(["chiffre_affaires"], group_by=["fournisseur.nom"])
print("  CA par fournisseur ->", fan["status"], fan["error"]["code"], ":", fan["error"]["message"][:110], "…")

title("4. QUESTION MÉTIER AVEC PROVENANCE (agent scripté, aucun LLM)")
print("Q : « Si Moulins de France est en rupture, quels clients et quelles commandes ouvertes sont touchés, "
      "et quel CA commandé au T3 cela représente-t-il ? »")
step = direction.resolve_entity("Moulins de France")
fournisseur = step["data"]["entity"]
print(f"  resolve_entity -> {fournisseur['label']} {fournisseur['iri']} (ids source {fournisseur['source_ids']})")
imp = direction.impact(fournisseur["iri"])["data"]["impacts"]
opened = [c for c in imp["commandes_impactees"]["items"] if c["ouverte"]]
clients = imp["clients_impactes"]["items"]
print(f"  impact structurel -> {len(clients)} clients : {[c['label'] for c in clients]}")
print(f"  commandes ouvertes : {[(c['label'], c['statut']) for c in opened]} (via faits {opened[0]['via_facts'] if opened else []})")
open_clients = sorted({direction.hub.kg.get(c["iri"], "passeePar") for c in opened})
print(f"  clients ayant une commande ouverte : {[direction.hub.kg.entity(c)['label'] for c in open_clients]}")
ca = direction.compute_metric(["chiffre_affaires"], group_by=["client.nom"],
                              filters={"client.iri": open_clients},
                              period_start="2026-07-01", period_end="2026-09-30")
print("  compute_metric CA T3 :", [(r["client.nom"], r["value"]) for r in ca["data"]["chiffre_affaires"]["rows"]])
print("  provenance du calcul :", {k: v for k, v in ca["provenance"][0].items() if k != "sql"})
print("  Réponse : chiffres issus de hub.semantic (modèle v%s), relations issues de faits vTiger (produit->fournisseur) "
      "et de l'inférence R2 (client dependDe fournisseur)." % hub.model.version)
print("\n  Cas d'ambiguïté / d'absence :")
print("   define('marge')          ->", direction.define("marge")["status"], direction.define("marge")["clarification"]["options"])
amb = bruno.resolve_entity("Martin")
print("   resolve('Martin') [Nord] ->", amb["status"], [c["label"] for c in amb["data"].get("candidates", [amb["data"].get("entity", {})])])
amb = direction.resolve_entity("Martin")
print("   resolve('Martin') [admin]->", amb["status"], [c["label"] for c in amb["data"]["candidates"]])
nd = direction.compute_metric(["chiffre_affaires"], period_start="2025-01-01", period_end="2025-12-31")
print("   CA 2025                  ->", nd["status"], [w for w in nd["warnings"] if "no_data" in w])
print("   define('EBITDA')         ->", direction.define("EBITDA")["status"])
grp = direction.resolve_entity("Clients Nord - commandes", "GroupeWhatsApp")["data"]["entity"]["iri"]
g = direction.get_entity(grp)
desc = [f for f in g["data"]["observed"] if f["predicate"] == "nad:description"][0]
print("   groupe WhatsApp : description untrusted =", desc.get("untrusted"), "| notice :", g["notices"][0][:90], "…")
jeanne = direction.resolve_entity("+33612345678", "IdentiteWhatsApp")["data"]["entity"]["iri"]
print("   identité WhatsApp +336…78 -> inféré :", [(f["value"], f["provenance"]["rule_id"]) for f in direction.get_entity(jeanne)["data"]["inferred"]])
dup = direction.resolve_entity("+33677889900", "IdentiteWhatsApp")["data"]["entity"]["iri"]
print("   identité WhatsApp +336…00 -> à vérifier :", [(f["target_label"], f["provenance"]["status"]) for f in direction.get_entity(dup)["data"]["to_verify"]])
print("   analyste (sans pii:read) voit le mobile de Jeanne :",
      [f["value"] for f in analyste.get_entity(direction.resolve_entity("CON1")["data"]["entity"]["iri"])["data"]["observed"] if f["predicate"] == "nad:mobile"])

title("5. ACTION AUTORISÉE : bruno.commercial.nord confirme SO106 (brouillon)")
so106 = bruno.resolve_entity("SO106")["data"]["entity"]["iri"]
print("  actions disponibles :", [(a["action"], a["allowed"], a["reason"]) for a in bruno.list_actions(so106)["data"]["actions"]])
r5 = bruno.execute_action("confirmer", so106, idempotency_key="demo-confirm-so106")
show({k: r5["data"][k] for k in ("numero", "statut_avant", "statut_apres", "effets", "source_ack", "simulated_source")})
print("  warnings :", r5.get("warnings"))
print("  rejeu même clé :", bruno.execute_action("confirmer", so106, idempotency_key="demo-confirm-so106")["data"]["replayed"])

title("6. MÊME ACTION REFUSÉE POUR UN RÔLE NON AUTORISÉ")
r6 = david.execute_action("confirmer", so106, idempotency_key="demo-confirm-so106-david")
print("  david.logisticien.lille :", r6["status"], r6["error"])
so107 = direction.resolve_entity("SO107")["data"]["entity"]["iri"]
r6b = hub.toolkit(P("chloe.commerciale.sud")).execute_action("annuler", so107, "demo-chloe", {"motif": "test hors périmètre"})
print("  chloe (Sud) annule SO107 (Nord) :", r6b["status"], r6b["error"]["code"])
r6c = agent.execute_action("annuler", so102, "demo-agent-cancel", {"motif": "demande trouvée dans un groupe WhatsApp"})
print("  agent IA annule SO102 :", r6c["status"], r6c["error"]["message"])
print("  statut SO102 inchangé :", direction.get_entity(so102)["data"]["inferred"][0]["value"])

title("(+) PROPAGATION D'UNE MODIFICATION SOURCE (delta vTiger : SO102 ligne 1 5->6, coût PRO1 21->22, contact CON4 supprimé)")
before = direction.compute_metric(["chiffre_affaires", "marge"], filters={"client.nom": "Boulangerie Martin"})
d = ingest(hub, "vtiger", hub.settings.demo_dir / "vtiger" / "delta_001_2026-09-30.json")
after = direction.compute_metric(["chiffre_affaires", "marge"], filters={"client.nom": "Boulangerie Martin"})
print("  delta :", d["stats"])
for m in ("chiffre_affaires", "marge"):
    print(f"  {m} Boulangerie Martin : {before['data'][m]['rows'][0]['value']} -> {after['data'][m]['rows'][0]['value']}")
ok, violations = validate(hub.kg)
print(f"  SHACL après delta : conforme={ok} ; violations={[(v['path'].split('#')[-1], v['message'][:70]) for v in violations]}")
print("  (référence pendante : SO104 pointe vers le contact supprimé ; la désynchronisation est détectée et non masquée)")

title("AUDIT")
ok, broken = hub.audit.verify()
print(f"  chaîne d'audit valide : {ok} ; entrées : {hub.conn.execute('SELECT COUNT(*) FROM audit_log').fetchone()[0]}")
for e in hub.audit.tail(6):
    print(f"  #{e['seq']} {e['actor']:<26} {e['kind']:<7} {e['action']:<26} {e['outcome']}")
