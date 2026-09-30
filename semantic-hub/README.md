# NAD Semantic Hub

Infrastructure métier réutilisable qui complète l'extension [`whatsapp-extractor`](../whatsapp-extractor)
(WhatsApp Web → vTiger CRM). Elle réunit en un seul endroit :

- une **ontologie** versionnée (glossaire YAML → OWL + SHACL) ;
- un **knowledge graph** alimenté par vTiger, le WMS et l'export WhatsApp de l'extension, avec la
  provenance de chaque fait ;
- un **modèle sémantique** analytique : CA, marge, panier moyen, délais… calculés une seule fois pour
  tous les consommateurs ;
- une **couche opérationnelle** : cycle de vie des commandes, actions contrôlées, RBAC/ABAC et audit ;
- une **interface agents IA** : API REST typée et serveur MCP.

Le prompt d'origine, adapté au contexte du dépôt, est dans [`docs/PROMPT_ADAPTE.md`](docs/PROMPT_ADAPTE.md).
L'architecture et les choix sont expliqués dans [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), et la
façon d'ajouter une entité, une métrique ou une action dans [`docs/EXTENDING.md`](docs/EXTENDING.md).

> **Ce qui est réel et ce qui est simulé.** Les données sont **fictives**. Les écritures vers vTiger et
> le WMS passent par des **passerelles simulées** (`sim_source_write`) : les réponses le signalent
> (`simulated_source: true`). Le client webservice vTiger (`VtigerWebserviceClient`,
> `VtigerLiveGateway`) est implémenté mais **n'a pas été testé contre une instance réelle**. Aucun WMS
> réel n'existe : il est représenté par des CSV. Le serveur MCP et l'API REST sont réels et testés.

## Démarrage rapide

```bash
cd semantic-hub
python3 -m pip install -r requirements-dev.txt
make demo            # démonstration complète en 6 étapes (+ propagation, audit)
make test            # 75 tests d'acceptation
```

Avec Docker :

```bash
docker compose up --build -d api          # API sur http://localhost:8000/docs (démo importée au 1er démarrage)
docker compose --profile test run --rm tests
# derrière un proxy TLS : docker build --secret id=pip_ca,src=/chemin/ca.crt .
```

Commandes utiles (`python -m hub.cli …`) :

| Commande | Rôle |
|---|---|
| `ingest-demo [--if-empty]` | importe taux de change, snapshot vTiger, WMS et export WhatsApp |
| `ingest vtiger <fichier>` | snapshot, ou delta si le fichier contient `updated`/`deleted` |
| `ingest wms <dossier>` / `ingest whatsapp <export.json>` | autres sources |
| `extract-vtiger <fichier>` | extrait un snapshot d'un vTiger réel (`NAD_VTIGER_*`), **non testé** |
| `validate` | validation SHACL du graphe (code retour 1 si non conforme) |
| `export-rdf <fichier.trig>` | export RDF, un graphe nommé par source |
| `audit-verify` | vérifie la chaîne de hachage du journal d'audit |
| `serve` / `mcp` | API REST / serveur MCP stdio |

## Arborescence

```
semantic-hub/
├── ontology/            glossary.yaml (SOURCE DE VÉRITÉ) → ontology.ttl, shapes.ttl (générés)
├── semantic_model/      model.yaml : dimensions, granularités, jointures, métriques, règles
├── operations/          order_lifecycle.yaml : états, transitions, contrats d'actions
├── security/            policies.yaml (RBAC+ABAC), principals.demo.yaml (empreintes de jetons)
├── hub/
│   ├── migrations/      001 knowledge graph + audit, 002 projections analytiques
│   ├── kg.py            entités, identités sources, faits bitemporels + provenance
│   ├── ingest/          vtiger.py (snapshot/delta + client webservice), wms.py, whatsapp.py, pipeline.py
│   ├── inference.py     R1 identité WhatsApp→Contact, R2 Client dépend de Fournisseur, R3 statut consolidé
│   ├── projection.py    KG → dim_* / fct_* (remises, retours, devises, fuseau, coût historique)
│   ├── semantic.py      compilation des métriques en SQL (anti double comptage, exclusions)
│   ├── operations.py    machine à états + actions (source d'abord, idempotence, désynchronisation)
│   ├── gateways.py      passerelles simulées / vTiger live
│   ├── security.py      authentification par jeton, permissions, périmètres, masquage PII
│   ├── audit.py         journal chaîné SHA-256
│   ├── rdf.py           génération OWL/SHACL, export TriG, validation pyshacl
│   ├── agent_tools.py   outils agents (enveloppe status/data/provenance/warnings/clarification)
│   ├── api.py           FastAPI (OpenAPI /docs)
│   └── mcp_server.py    serveur MCP (SDK v2)
├── data/demo/           vTiger (snapshot + delta), WMS (CSV), WhatsApp (format de l'extension), taux
├── scripts/             demo.py, build_ontology.py
├── tests/               75 tests + oracle.py (recalcul indépendant des métriques)
├── Dockerfile, docker-compose.yml, Makefile, .env.example, mcp.example.json
```

## API REST (extraits)

```bash
T="Authorization: Bearer demo-analyste"
curl -H "$T" "localhost:8000/definitions?term=chiffre_affaires"
curl -H "$T" "localhost:8000/entities/resolve?q=SO102"
curl -H "$T" "localhost:8000/entities/relations?iri=urn:nad:commande:…&depth=2"
curl -H "$T" -X POST localhost:8000/metrics/query -H 'Content-Type: application/json' \
     -d '{"metrics":["chiffre_affaires","marge"],"group_by":["client.nom"],"period_start":"2026-07-01","period_end":"2026-09-30"}'
curl -H "Authorization: Bearer demo-commercial-nord" -H "Idempotency-Key: 7f3a…" -X POST \
     localhost:8000/actions/confirmer -H 'Content-Type: application/json' -d '{"commande":"urn:nad:commande:…"}'
```

Codes HTTP : 401 (jeton), 403 (droit ou périmètre), 404, 409 (transition invalide, conflit
d'idempotence, stock), 422 (validation), 503 (source indisponible). Les réponses `ambiguous` et
`no_data` renvoient 200 avec la clarification ou l'avertissement.

## Agents IA (MCP)

`mcp.example.json` contient une configuration prête à l'emploi (Claude Code, Claude Desktop ou tout
client MCP). Le principal est fixé côté serveur par `NAD_MCP_TOKEN`. Dans la démo, `demo-agent-nord`
est un commercial Nord marqué `is_agent` : il peut confirmer, mais il ne peut ni annuler, ni voir la
marge, ni lire hors de sa région.

## Démonstration (`make demo`), résultats obtenus

| Étape | Résultat |
|---|---|
| 1. Import | 343 faits vTiger, 115 WMS, 19 WhatsApp (6 enregistrements → 5 identités, 1 doublon fusionné, 4 champs ignorés par minimisation) ; réimport : 343 inchangés ; SHACL conforme |
| 2. Relations de SO102 | client, contact, entrepôt (WMS), lignes → produits, identité WhatsApp (R1), dépendances fournisseurs (R2) ; chaque lien porte sa source |
| 3. CA / marge T3 2026 | CA 4 070,40 €, marge 1 456,40 €, taux 39,1 %, panier 678,40 €, délai 35,5 h, annulation 22,2 % ; SO109 (GBP sans taux) et le coût manquant des gobelets **exclus et signalés** ; CA par fournisseur **refusé** (N-N) |
| 4. Question avec provenance | fournisseur → produits → commandes ouvertes → clients → CA, avec IRI, identifiants de faits, versions et exécutions d'ingestion ; « marge » et « Martin » → clarification ; CA 2025 → `no_data` ; « EBITDA » → `not_found` |
| 5. Action autorisée | `bruno.commercial.nord` confirme SO106 : brouillon → confirmée (source simulée), rejeu idempotent |
| 6. Refus | logisticien → `FORBIDDEN` ; commerciale Sud → `OUT_OF_SCOPE` ; agent IA qui annule → `FORBIDDEN` |
| (+) Propagation | delta vTiger : CA Boulangerie Martin 1 049 → 1 094 €, coût historique conservé pour les commandes passées, contact supprimé → référence pendante **détectée** par SHACL |

## Passage en production (étapes nécessaires)

1. Confirmer les hypothèses (H) : champs `cf_*` de vTiger, fuseau des dates webservice, WMS et
   format de ses événements, référentiel de taux, responsables métier du glossaire.
2. Brancher `extract-vtiger` (ou l'opération `sync` avec `modifiedTime`) et tester `VtigerLiveGateway`
   sur une instance de recette avec un utilisateur API aux droits minimaux.
3. Remplacer `principals.demo.yaml` par un fournisseur d'identité (OIDC) et stocker les secrets dans
   un coffre (Vault ou équivalent).
4. Passer à PostgreSQL si la charge ou la concurrence l'exige, planifier les ingestions (cron ou
   workflow), rendre les projections incrémentales et superviser `/health` (`projection_stale`).
5. Faire évoluer l'extension Chrome : remplacer l'écriture directe dans vTiger par un appel au hub
   (résolution d'identité R1 et audit centralisés).
