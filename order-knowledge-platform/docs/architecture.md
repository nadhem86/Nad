# Architecture

## 1. Contexte et hypothèses

Le contexte réel (domaine, sources, stack, consommateurs, contraintes) n'étant pas fourni, la plateforme
implémente l'exemple **gestion de commandes** avec quatre systèmes sources **simulés** :

| Système | Rôle simulé | Format de démo | Fait autorité pour |
|---|---|---|---|
| ERP | Clients, produits, commandes, lignes, taux de change | SQLite + journal de changements (CDC par triggers) | Statut et contenu des commandes, référentiel client/produit |
| CRM | Contacts (segment, téléphone, consentement) | CSV (instantané) | Segment, téléphone, consentement marketing |
| WMS | Entrepôts, stocks | CSV (instantanés) | Entrepôts, stock |
| SRM | Fournisseurs et produits fournis | JSON (export) | Fournisseurs, relation produit → fournisseur |

Hypothèses à confirmer : volumétrie modeste (≤ quelques millions de faits), un fuseau de référence
(Europe/Paris), une devise de reporting (EUR), un seul tenant, authentification par clé API en démo.

## 2. Les quatre couches et ce qu'elles partagent

```
            models/glossary.yaml ──► generated/ontology.ttl (OWL)  +  generated/shapes.ttl (SHACL)
                   │  (concepts, propriétés, relations, contraintes, correspondances sources)
                   ▼
 Sources ──► Ingestion ──► entity / id_map / fact  (KNOWLEDGE GRAPH : faits + provenance + inférences)
 (ERP, CRM,     │                    │
  WMS, SRM)     └──► tables core ◄───┘  même transaction, mêmes identifiants canoniques
                         │
                         ├──► vues v_fact_* / v_dim_*  ◄── models/semantic_model.yaml (MODÈLE SÉMANTIQUE)
                         │
                         └──► actions ◄── models/operations.yaml (COUCHE OPÉRATIONNELLE)
                                   │           exécutées par l'ERP (autorité) puis resynchronisées
                                   ▼
            okp/services.py (Platform) ◄── models/policies.yaml (RBAC + ABAC + plafonds de canal)
              │            │             │
          API REST     outils agents    serveur MCP        (+ CLI)
```

Ces couches ne forment pas une hiérarchie : ce sont des **vues spécialisées** qui partagent :

- **les mêmes identifiants canoniques** (`CUS-…`, `ORD-…`, `OLN-…`, `PRD-…`, `WHS-…`, `SUP-…`) — clés des tables
  core, sujets des faits, IRI RDF (`https://example.org/okp/id/ORD-…`), clés des dimensions et paramètres des actions ;
- **les mêmes définitions** : le glossaire définit les propriétés (types, obligatoires, sensibilité). Le modèle
  sémantique référence ses concepts (`dimension.concept`). La machine à états réutilise l'énumération
  `Order.status`. Un chargement de modèles incohérents échoue (`okp/registry.py`), et les tests `tests/test_models.py`
  vérifient les correspondances entre couches.

| Couche | Question traitée | Représentation | Code |
|---|---|---|---|
| Ontologie | Qu'est-ce qu'un Client ? Quelles relations sont possibles ? | YAML → OWL + SHACL | `models/glossary.yaml`, `okp/ontology/` |
| Knowledge graph | Quels faits sont connus, d'où viennent-ils, depuis quand ? | Table `fact` (quads + provenance), export TriG | `okp/ingestion/`, `okp/kg/` |
| Modèle sémantique | Comment calculer le CA sans double comptage ? | YAML → SQL paramétré sur vues | `models/semantic_model.yaml`, `okp/semantic/` |
| Opérationnel | Que peut-on faire sur une commande, qui peut, à quelles conditions ? | YAML + préconditions Python | `models/operations.yaml`, `okp/operations/` |

**Classe vs instance** : `okp:Customer` est une classe OWL (dans `ontology.ttl`). `inst:CUS-6D18EA5022 a okp:Customer`
est une instance, présente seulement dans le knowledge graph. Les propriétés du glossaire sont des
prédicats de la forme `okp:customer.region`.

## 3. Choix technologiques

**Stockage relationnel (SQLite) portant un graphe de faits, avec export RDF.** Justification :

- *RDF/OWL + SHACL* sont retenus pour **décrire** l'ontologie et **valider** en monde fermé, parce que ce sont des standards
  interopérables et que SHACL exprime cardinalités et règles conditionnelles. Un raisonneur OWL (monde ouvert) ne détecte pas une
  donnée manquante. Nous n'en utilisons pas : toutes les contraintes sont vérifiées par SHACL, SQL, l'ingestion
  ou les préconditions d'action (colonne `enforced_in` du glossaire).
- Un *triple store dédié* ou un *property graph* (Neo4j…) ajouterait une technologie et un moteur de requêtes. Or les
  besoins de parcours sont bornés (voisinage ≤ 3 sauts, impacts, chemin court). Ces parcours sont servis par la
  table `fact` indexée. Le modèle sémantique a de toute façon besoin d'un moteur SQL (agrégations, jointures many-to-one).
- Le **même moteur** porte donc la projection analytique, le graphe de faits, l'idempotence et l'audit, et il est
  **transactionnel** : une ingestion met à jour core, faits et inférences atomiquement.
- Évolution : si les parcours deviennent dominants, il suffit d'exporter la table `fact` vers un triple store
  (l'export TriG existe : `python -m okp export-rdf`) ou vers un property graph, sans changer les modèles.

SQLite convient à la démonstration locale. En production, on passe à **PostgreSQL** : les vues et le SQL sont standards,
à l'exception de `julianday`/`date()`. Ce portage n'est pas implémenté ici.

## 4. Knowledge graph : faits et provenance

Chaque ligne de `fact` est un fait courant ou historique :

| Champ | Contenu |
|---|---|
| `subject`, `predicate`, `object_id` \| `object_value`, `qualifiers` | Le fait lui-même (relation ou littéral JSON, qualificatifs de relation). |
| `source_system`, `source_entity`, `source_id` | Enregistrement source d'origine (`erp/ERP_ORDER/SO-10001`), ou `okp/inference/R-…`. |
| `collected_at`, `run_id` | Date de collecte et exécution d'ingestion. |
| `valid_from`, `valid_to` | Période de validité **métier** (horodatage source). `NULL` = inconnue (jamais inventée). |
| `superseded_at` / `retracted_at` | Fin technique (remplacé par une nouvelle valeur / retiré). L'historique est conservé. |
| `method` | `import`, `manual` (réservé) ou `inference`. |
| `rule_id`, `derived_from` | Obligatoires pour une inférence : règle et identifiants des faits sources (contrainte SQL). |
| `validation_status` | `validated`, `pending_review` (ex. rapprochement d'identité incertain) ou `rejected`. |

**Ingestion** (`okp/ingestion/pipeline.py`) :

1. Extraction (complète, ou CDC pour l'ERP en mode incrémental).
2. Déduplication intra-source (CRM : un contact par email normalisé).
3. Correspondance vers le glossaire, puis validation (types, obligatoires, bornes, énumérations, motifs). Un enregistrement
   invalide est mis en **quarantaine** (`ingestion_issue`) et le dernier état valide est conservé.
4. Résolution d'identité. Chaque concept a une **source d'ancrage** qui produit l'identifiant canonique
   (UUIDv5 déterministe). Les sources d'enrichissement sont rattachées par règle (`ID-EMAIL`). Un rapprochement
   incertain (`ID-NAME-COUNTRY`) produit un fait `possibleCrmMatch` en `pending_review` et **ne fusionne pas**.
5. **Idempotence** : l'empreinte SHA-256 de chaque enregistrement est comparée à `source_record`. Un enregistrement
   inchangé n'est pas retraité, et réimporter ne crée aucun fait (testé).
6. Projection core (colonnes dont la source fait autorité) et **diff des faits** par enregistrement source.
7. Suppressions : un enregistrement absent de l'instantané (mode complet) ou un événement `D` du CDC entraîne
   la rétraction des faits et la suppression logique de l'entité et de la ligne core. Pour une source
   d'enrichissement, seules ses colonnes d'autorité sont remises à NULL.
8. Recalcul des inférences (`okp/ingestion/inference.py`) : ajout, rétraction ou maintien, de façon idempotente.

Requêtes de relations (`okp/kg/queries.py`, exposées par `services.py`) : voisinage à N sauts
(sens `out` = dépendances, `in` = impacts), chemin le plus court, vue commande, impact fournisseur.

## 5. Source de vérité et désynchronisations

| Donnée | Source de vérité | Représentations dérivées |
|---|---|---|
| Statut, dates, lignes de commande | ERP | core `sales_order`/`order_line`, faits `order.*`, vues analytiques, inférences |
| Identité client, région, statut | ERP | core `customer`, faits `customer.*` |
| Segment, téléphone, consentement | CRM | colonnes core correspondantes, faits `customer.segment`… |
| Entrepôts, stocks | WMS | core `warehouse`, `inventory_snapshot` |
| Fournisseurs, produit → fournisseur | SRM | core `supplier`, `product_supplier`, faits `suppliedBy` |
| Définitions métier, métriques, règles | `models/*.yaml` (Git) | OWL/SHACL générés, vues, moteur de métriques |
| Faits inférés | Règles `R-*` | table `fact` (method = inference) |

Traitement des écarts :

- **Action métier** : l'ERP est modifié en premier, avec un contrôle de concurrence optimiste (statut attendu).
  Ensuite, une resynchronisation ciblée emprunte **le même chemin que l'ingestion** (une seule logique de projection).
  Si elle échoue, l'action reste réussie dans la source, un événement est placé dans `sync_outbox` et la réponse
  indique `sync_status = pending`. La reprise se fait par `python -m okp sync`.
- **Modification directe dans une source** : le CDC ou un instantané est appliqué à la prochaine ingestion
  incrémentale. Si une action est tentée sur un état obsolète, elle est refusée en `CONFLICT`.
- **Contrôle** : `python -m okp reconcile` compare core, entités, identifiants et valeurs des faits courants, et signale
  les faits orphelins et la file de resynchronisation en attente. `python -m okp validate` exécute SHACL.

## 6. Arborescence

```
order-knowledge-platform/
├── models/                 modèles métier versionnés (source des définitions)
│   ├── glossary.yaml       concepts, propriétés, relations, cardinalités, contraintes, correspondances
│   ├── semantic_model.yaml dimensions, faits (grain), jointures autorisées, métriques et règles
│   ├── operations.yaml     états, transitions, actions (contrats, préconditions, erreurs…)
│   ├── policies.yaml       rôles, permissions, portées, plafonds par canal
│   └── CHANGELOG.md
├── generated/              ontology.ttl, shapes.ttl (générés, vérifiés par test)
├── config/principals.demo.yaml   principaux de démo (empreintes de clés)
├── okp/
│   ├── registry.py         chargement + validation croisée des modèles
│   ├── db.py, migrations/  schéma versionné (core, KG, opérations/audit, vues analytiques)
│   ├── ingestion/          sources, validation, pipeline, inférences
│   ├── ontology/           génération OWL/SHACL, export RDF, validation pyshacl
│   ├── kg/queries.py       requêtes de graphe
│   ├── semantic/engine.py  moteur de métriques
│   ├── operations/         préconditions, passerelle ERP (simulée), service d'actions
│   ├── security/           authentification, politique RBAC/ABAC, audit chaîné
│   ├── services.py         façade unique appliquant la politique
│   ├── api/app.py          API REST FastAPI
│   ├── agent/              outils agents + serveur MCP
│   ├── demo/               générateur de sources fictives + scénario
│   └── cli.py              `python -m okp …`
├── tests/                  102 tests
├── docs/                   architecture, extension, interface agents, gouvernance
├── Dockerfile, docker-compose.yml, Makefile, .env.example
```
