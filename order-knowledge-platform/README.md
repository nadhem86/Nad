# Order Knowledge Platform

Infrastructure métier réutilisable qui externalise les connaissances aujourd'hui dispersées dans les tables, les
jointures SQL, les dashboards et le code. Elle fournit, avec **des définitions et des identifiants communs** :

- une **ontologie** versionnée (glossaire YAML → OWL + SHACL) ;
- un **knowledge graph** de faits avec provenance, historique et inférences traçables ;
- un **modèle sémantique analytique** (métriques, grains, jointures autorisées, règles d'agrégation) ;
- une **couche opérationnelle** (machine à états, actions contrôlées, idempotence, autorité de la source) ;
- une **politique d'accès** RBAC + ABAC appliquée côté serveur, un **journal d'audit** infalsifiable ;
- des **interfaces** : API REST typée (OpenAPI), outils pour agents IA (HTTP et **MCP**), CLI.

Démonstration : gestion de commandes (Client, Commande, LigneCommande, Produit, Entrepôt, Fournisseur) avec quatre
systèmes sources **fictifs et simulés** (ERP, CRM, WMS, SRM). Aucune intégration réelle à un système tiers n'est
fournie : la passerelle ERP écrit dans une base SQLite qui joue le rôle de l'ERP.

## Démarrage rapide

```bash
cd order-knowledge-platform
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

python -m okp demo            # génère les sources fictives, importe, et déroule les 6 étapes de la démo
python -m pytest              # 102 tests
python -m okp serve           # API : http://127.0.0.1:8000/docs  (en-tête X-API-Key, clés dans .env.example)
OKP_API_KEY=demo-analyst-key python -m okp mcp   # serveur MCP (stdio)
```

Avec Docker :

```bash
cp .env.example .env          # puis définir OKP_AUDIT_HMAC_KEY
make docker-init              # génère + importe les données fictives dans le volume
make docker-up                # API sur http://127.0.0.1:8000
```

Commandes utiles (`python -m okp --as <principal> …`) : `ingest --mode full|incremental`, `validate` (SHACL),
`reconcile` (cohérence entre couches), `audit-verify`, `export-rdf`, `define <terme>`, `resolve <id>`,
`relations <id>`, `metric revenue --by customer.region --from 2026-01-01`, `actions SO-20001`,
`act confirm_order SO-20001 --key ma-cle-unique`, `sync` (reprise de synchronisation).

## Ce que montre `python -m okp demo`

| Étape | Résultat (extrait réel de l'exécution) |
|---|---|
| 1. Import | 208 enregistrements, 1 730 faits sources + 167 faits inférés. Mis en quarantaine ou en revue : 1 doublon CRM, 1 prospect, 1 rapprochement incertain (`pending_review`), 1 ligne invalide. **Réimport : 0 fait créé.** SHACL conforme (2 122 triplets). Réconciliation cohérente. |
| 2. Relations de SO-10001 | Client, lignes → produits → fournisseurs, entrepôt : chaque relation cite son enregistrement source. La dépendance fournisseur est marquée *inférée* (`R-SUPPLY-DEP` et identifiants des faits sources). |
| 3. CA et marge | CA net 225 637,97 EUR (2 lignes exclues faute de taux de change, signalées). Marge 96 304,77 EUR (1 ligne sans coût, signalée). Taux 42,8 %. La commerciale IDF est refusée sur la marge et ne voit que le CA IDF. |
| 4. Question métier | « Retard de Nordic Components ? » → 8 commandes en cours et 4 clients, avec provenance. « Ventes de mars ? » → `ambiguous`. « CA 2027 ? » → `no_data`. La note client contenant une injection est renvoyée comme `untrusted_text`. |
| 5. Action autorisée | La commerciale IDF confirme SO-20001 : l'ERP est mis à jour (autorité), puis resynchronisé, et l'historique de statut est conservé. Le rejeu avec la même clé renvoie `replayed=true`. |
| 6. Action refusée | Analyste → `FORBIDDEN` ; commerciale IDF sur une commande ARA → `NOT_FOUND` ; responsable via un agent → annulation refusée (plafond du canal). Audit : chaîne intègre. |

Principaux de démo : `alice.admin`, `bruno.manager` (responsable ADV), `chloe.rep` (commerciale IDF),
`david.rep` (ARA/OCC), `emma.ops` (entrepôt LYS1), `farid.analyst`, `ingest.bot`.

## Documentation

- [docs/architecture.md](docs/architecture.md) : couches, identifiants partagés, choix (relationnel + RDF/SHACL), provenance,
  source de vérité et traitement des désynchronisations, arborescence.
- [docs/extending.md](docs/extending.md) : ajouter une entité, une métrique, une action.
- [docs/agent-interface.md](docs/agent-interface.md) : outils, garanties, configuration MCP.
- [docs/governance.md](docs/governance.md) : RBAC/ABAC, audit, secrets, données personnelles, versionnement.
- Modèles : [models/](models/) · journal : [models/CHANGELOG.md](models/CHANGELOG.md)

## Couverture des tests (102)

| Fichier | Vérifie |
|---|---|
| `test_models.py` | Cohérence glossaire ↔ tables core ↔ vues ↔ préconditions ↔ permissions ; ontologie générée à jour |
| `test_ingestion.py` | Idempotence (complet et incrémental), identifiants déterministes, résolution d'identité, doublons, prospect, quarantaine, **propagation d'une modification source** vers core, KG (historique) et métriques, suppressions (ERP et fichiers) |
| `test_kg.py` | Conformité SHACL, détection de violations, provenance de chaque fait, traçabilité des inférences et séparation en graphes nommés, règle d'inférence, requêtes de dépendances et d'impact, cohérence des identifiants |
| `test_metrics.py` | Exactitude comparée à un **oracle indépendant** calculé sur les tables ERP brutes ; absence de double comptage (16 combinaisons) ; refus du fan-out ; ratios non additifs ; stock semi-additif ; fuseau horaire ; devises ; valeurs manquantes ; `no_data` ; injection SQL neutralisée |
| `test_operations.py` | Transitions invalides sans effet, préconditions, idempotence et conflit de clé, autorité de l'ERP, conflit de concurrence, source indisponible, échec de resynchronisation → outbox → reprise |
| `test_security.py` | Refus par rôle, périmètres région et entrepôt, masquage des champs, plafond du canal agent, authentification, audit append-only et détection d'altération, absence de données personnelles dans l'audit, secret obligatoire en production |
| `test_agent.py` | Schémas des outils, définitions uniquement issues des modèles, ambiguïtés (terme et entité), `unknown_metric`, `no_data`, provenance observée/inférée, contenu source non fiable, impossibilité d'élever ses droits, **serveur MCP réel** (transport en mémoire du SDK) |
| `test_api.py` | Authentification HTTP, codes d'erreur, parcours de démo, en-tête `Idempotency-Key`, routes agent sur le canal agent |

## Limites connues

- **Sources simulées** : ERP (SQLite avec CDC par triggers), CSV et JSON. Aucun connecteur réel (API ERP,
  Debezium…). La passerelle `ErpGateway` est un bouchon à remplacer, avec la même interface.
- **SQLite** et un verrou global par processus : cela convient à une démonstration mono-instance, pas à la
  concurrence ni à la volumétrie. Le portage PostgreSQL n'est pas fait.
- **Authentification par clé API** statique ; pas d'OIDC, de rotation de clés ni de coffre à secrets.
- Le suivi des **retours** se fait à la période de la commande, pas à celle du retour. Il n'y a pas de gestion
  des avoirs ni des taxes par pays (la TVA est informative et exclue).
- Les inférences sont **recalculées entièrement** à chaque ingestion, ce qui ne passe pas à grande échelle (il faudrait
  un recalcul incrémental). Les preuves d'une inférence ne sont pas bornées en nombre.
- La **validation SHACL** porte sur tout le graphe (environ 5 s sur la démo) et n'est pas intégrée au flux
  d'ingestion (commande séparée).
- Le **droit à l'effacement** (purge des valeurs historiques de faits) et les durées de conservation ne sont pas implémentés.
- Pas d'export vers **Power BI** : les dashboards consomment `/metrics/query` (JSON). La génération de mesures DAX ou
  d'un modèle tabulaire à partir de `semantic_model.yaml` reste à faire.
- L'« agent » de la démo est **scripté** (aucun appel à un LLM). Le serveur MCP est testé avec le client du SDK `mcp`,
  pas avec un client LLM réel.

## Passage en production (étapes nécessaires)

1. Confirmer le périmètre avec les responsables métier : définitions, propriétaires, règles de métriques (retours,
   taxes, devises) et sensibilité des champs. Faire valider le glossaire (workflow de revue sur `models/`).
2. Remplacer les adaptateurs simulés par des connecteurs réels : CDC (Debezium/outbox) ou API pour l'ERP, API CRM,
   WMS et SRM. Remplacer `ErpGateway` par un client de l'API ERP, avec gestion des délais et reprises.
3. Porter le stockage sur PostgreSQL (migrations, vues). Envisager un triple store pour SPARQL fédéré si le besoin est avéré.
4. Brancher l'authentification sur l'IdP de l'entreprise (OIDC), mapper rôles et attributs depuis les groupes, et
   stocker les secrets dans un coffre.
5. Mettre en place l'observabilité (métriques d'ingestion, latence, alertes sur `reconcile` et sur la file
   `sync_outbox`), l'ordonnancement des ingestions et des sauvegardes.
6. Réaliser une analyse d'impact RGPD : conservation, effacement, registre des traitements.
7. Intégrer les consommateurs : connecteur Power BI (web ou modèle généré), clients MCP des agents avec comptes de
   service dédiés et plafonds de canal, et tests de charge.
