# Architecture : NAD Semantic Hub

## 1. Analyse du contexte

Le dépôt contient une seule application : l'extension Chrome `whatsapp-extractor`. Elle extrait des
contacts, groupes et conversations de WhatsApp Web, puis crée ou met à jour des Contacts ou Leads dans
vTiger CRM. La connaissance métier y est dispersée :

| Connaissance | Où elle se trouvait | Où elle est désormais |
|---|---|---|
| « Un contact = un numéro » | `vtiger.js` (`findContactByPhone`, variantes de format) | `ids.normalize_phone` + règle d'inférence R1 (traçable, gère l'ambiguïté) |
| Correspondance des champs WhatsApp → vTiger | `VtigerClient.mapContact` | glossaire (`source_ids`) + `ingest/*.py` |
| Doublons | options « skip / update / create » dans le popup | résolution d'identité (`source_identity`) + réconciliation idempotente |
| Statuts de commande, CA, marge | *inexistants* : ils seraient calculés dans vTiger ou dans des tableaux Excel | `order_lifecycle.yaml`, `model.yaml` |

**Informations bloquantes identifiées** (non bloquantes pour la démo, à confirmer pour la production) :
1. Le WMS réel (s'il existe) et son format d'échange. Il est simulé ici par des CSV.
2. Les champs personnalisés vTiger (`cf_region`, `cf_date_confirmation`, `cf_date_annulation`,
   `cf_motif_annulation`) et le fuseau horaire des dates renvoyées par le webservice (supposé UTC).
3. Le référentiel de taux de change à utiliser.
4. L'annuaire d'entreprise (OIDC/LDAP) qui remplacera `principals.demo.yaml`.

## 2. Les quatre couches et leurs identifiants partagés

```
                 ┌───────────────── ontology/glossary.yaml (v1.0.0) ─────────────────┐
                 │ concepts, propriétés, relations, cardinalités, PII, non-fiable     │
                 └──────┬───────────────────────┬───────────────────────────┬──────────┘
          génère        │ valide (SHACL)        │ référence (nad:Commande…)  │ référence (états = énum statut)
     ontology.ttl       │                        │                            │
     shapes.ttl         ▼                        ▼                            ▼
  ┌──────────────┐  ┌───────────────────┐  ┌────────────────────────┐  ┌──────────────────────────┐
  │  ONTOLOGIE   │  │ KNOWLEDGE GRAPH   │  │ MODÈLE SÉMANTIQUE      │  │ COUCHE OPÉRATIONNELLE    │
  │ classes,     │  │ entity +          │─▶│ dim_* / fct_* (projec- │  │ états, transitions,      │
  │ propriétés,  │  │ source_identity + │  │ tions, clés = IRI)     │  │ actions, permissions     │
  │ contraintes  │  │ fact (provenance) │  │ model.yaml → SQL       │  │ order_lifecycle.yaml     │
  └──────────────┘  └─────────▲─────────┘  └───────────▲────────────┘  └────────────┬─────────────┘
                              │ ingestion              │ rebuild                     │ 1) écrit la source
                     vTiger / WMS / WhatsApp            └──── après ingestion/action ─┘ 2) faits saisie
```

Ces couches ne forment pas une hiérarchie : chacune a son rôle et sa propre technologie. Elles
partagent trois éléments :
- **Les identifiants.** Chaque instance a une IRI `urn:nad:<slug>:<uuid5>`. Cette IRI sert de sujet
  des faits, de clé des tables `dim_*`/`fct_*`, de cible des actions et de valeur de filtre des métriques
  (`client.iri`). Un test vérifie que toute clé analytique est une IRI du graphe, du bon concept.
- **Les noms.** Un prédicat de fait est le nom d'une propriété du glossaire. Un état du cycle de vie
  est une valeur de l'énumération `Commande.statut`. Les dimensions du modèle sémantique pointent vers
  `nad:<Concept>`.
- **Les versions.** Chaque réponse d'API ou d'agent cite `glossary_version` et `model_version`.

| Couche | Répond à | Fichiers |
|---|---|---|
| Ontologie | Qu'est-ce qu'une Commande ? Quelles relations sont possibles, avec quelles cardinalités ? | `ontology/glossary.yaml` → `ontology.ttl`, `shapes.ttl` |
| Knowledge graph | Quelle commande a passé quel client, selon quelle source, depuis quand ? | `hub/kg.py`, `hub/ingest/`, `hub/inference.py` |
| Modèle sémantique | Comment calcule-t-on le CA ? À quelle granularité ? Quelles jointures sont interdites ? | `semantic_model/model.yaml`, `hub/semantic.py`, `hub/projection.py` |
| Opérationnel | Qui peut confirmer quoi, dans quel état, avec quel effet ? | `operations/order_lifecycle.yaml`, `hub/operations.py`, `security/` |

## 3. Choix techniques justifiés

**Stockage du graphe : relationnel (SQLite), exporté en RDF.** Trois options ont été comparées :

| Option | Pour | Contre dans ce contexte |
|---|---|---|
| Triplestore RDF (Fuseki, GraphDB) | SPARQL, standards | Un serveur de plus à opérer. La provenance par fait (RDF-star ou graphes nommés) et l'historique bitemporel restent à construire. |
| Property graph (Neo4j) | Parcours rapides | Licence et serveur supplémentaires. Pas de validation standard équivalente à SHACL. |
| **Table de faits relationnelle** | Zéro infrastructure. Provenance, validité et historique sont de simples colonnes. Transactions ACID partagées avec l'audit et l'idempotence. | Les parcours profonds sont moins rapides : négligeable à l'échelle d'une PME. |

Le graphe reste un graphe : les faits sont des triplets (`subject`, `predicate`, `object_iri|object_value`).
Il est exportable en **TriG** (`hub.cli export-rdf`), avec un graphe nommé par source, et validé
par **SHACL** (pyshacl) à partir de formes générées depuis le glossaire. Le passage à PostgreSQL ne
demande que de changer le pilote : le SQL utilisé est standard, à `julianday` près.

**RDF/OWL + SHACL pour l'ontologie.** OWL sert à la publication et à l'interopérabilité : classes,
propriétés fonctionnelles, `skos:definition`, `skos:altLabel`. SHACL sert à la validation
structurelle : types, cardinalités, énumérations, bornes et `lessThanOrEquals`. **Aucun raisonneur
n'est chargé de valider les règles métier.** Les transitions, les permissions et le stock sont
vérifiés par du code déterministe et testé.

**Une seule stack (Python 3.11).** FastAPI pour l'API typée et OpenAPI, SDK MCP v2 pour les agents,
rdflib et pyshacl pour RDF. Pas de broker, pas de cache, pas de base graphe.

## 4. Sources de vérité et désynchronisations

| Donnée | Autorité | Écrite par le hub ? |
|---|---|---|
| Client, Contact, Produit (prix, coût), Fournisseur, en-tête et lignes de Commande | vTiger | uniquement `sostatus` et dates via les actions `confirmer`/`annuler`, **d'abord dans vTiger** |
| Entrepôt, affectation, expédition, livraison, retours, stock | WMS (simulé) | via `affecter_entrepot`/`expedier`, d'abord dans le WMS |
| Identité et groupes WhatsApp | export de l'extension | non |
| Statut consolidé, liens WhatsApp → Contact, dépendances Client → Fournisseur | **hub (inférence)** | recalculés, jamais saisis |
| Métriques | **hub (modèle sémantique)** | projections reconstruites |
| Taux de change | référentiel CSV (H) | non |

Traitement des désynchronisations :
- Une **source n'écrase jamais une autre source** : la réconciliation se fait par couple (sujet, source).
- **Conflit de statut** entre vTiger (`Approved`) et le WMS (`expediee`) : règle R3 explicite et priorisée.
- **Action** : écriture dans la source d'abord. Si elle échoue, le graphe reste intact
  (`SOURCE_UNAVAILABLE`). Si elle réussit mais que le graphe échoue, on journalise `kind=desync` et on
  renvoie `SYNC_PENDING`. La prochaine ingestion rétablit l'état, car la source fait foi (testé).
- **Projections** : `projection_state` contient une empreinte du graphe, et `/health` indique
  `projection_stale`.
- **Références pendantes** (contact supprimé mais toujours référencé par une commande) : détectées par
  SHACL et non masquées (démontré après le delta).

## 5. Knowledge graph : cycle de vie d'un fait

```
source record ─▶ ensure_entity (source_identity → IRI) ─▶ upsert_facts(source) ─┬─ identique : rien (idempotent)
                                                                                 ├─ modifié : ancien.superseded_at, valid_to
                                                                                 └─ absent  : retiré (valeur_retiree)
snapshot : entité absente → retract_entity (tombstone deleted_at, faits retirés, cascade lignes)
delta    : liste `deleted` explicite (format vTiger sync)
WhatsApp : export partiel → aucune suppression déduite
inférence: replace_inferred(rule) : dérivation complète, derived_from = prémisses, a_verifier si ambigu
```

Colonnes de provenance : `source`, `source_record_id`, `method` (import | saisie | inference),
`rule_id`, `derived_from`, `confidence`, `status` (valide | a_verifier | rejete), `collected_at`,
`valid_from`/`valid_to` (validité métier), `recorded_at`/`superseded_at` (temps transactionnel),
`run_id`, `actor`.

## 6. Modèle sémantique

- Tables de faits et granularité : `fct_commande` (1 ligne par commande), `fct_ligne` (1 ligne par
  ligne de commande), `fct_stock` (1 ligne par jour × produit × entrepôt).
- Les **règles ligne par ligne** (remises, retours au prorata, conversion au taux du jour local, coût
  valide à la date de commande) sont appliquées **une seule fois** dans la projection.
- Le **moteur** n'utilise que les jointures déclarées. Il refuse une dimension N-N (`FAN_OUT`) ou
  interdite. Il ajoute les filtres communs (`commandes_valides`) et le périmètre régional du
  principal. Il compte les lignes exclues par motif. Enfin, il distingue `no_data` de `0`.
- Additivité : additive (CA, quantités, marge, nb commandes hors produit), semi-additive (stock :
  dernier relevé de la période), non additive (taux de marge, panier moyen, délai, taux d'annulation,
  toujours recalculés à partir des sommes).

## 7. Sécurité et gouvernance

- **RBAC + ABAC** (`security/policies.yaml`) : lecture par concept, `pii:read`, `metric:<id>`,
  `action:<id>`, périmètres `regions` (clients) et `entrepots` (logistique). L'attribut `is_agent`
  n'ouvre aux agents que les actions `agent_allowed: true`.
- **Côté serveur, pour toutes les interfaces** : API REST, MCP et CLI passent par `AgentToolkit`
  puis `OrderService`. Le principal MCP est fixé par `NAD_MCP_TOKEN`, jamais par l'agent.
- **Secrets** : `.env` (non versionné), empreintes SHA-256 des jetons, clé vTiger lue dans
  l'environnement uniquement.
- **Minimisation RGPD** : contenus de messages, statuts « à propos » et conversations WhatsApp non
  ingérés. PII masquées sans `pii:read`.
- **Audit chaîné** (SHA-256 de l'entrée précédente) : lectures d'outils, actions, refus, ingestions
  et désynchronisations. `hub.cli audit-verify` détecte toute altération.
- **Versionnement** : glossaire, modèle sémantique, cycle de vie et politique ont chacun un champ
  `version` ; les migrations SQL sont numérotées et tracées dans `schema_migrations`.

## 8. Interface agents

Les outils (`define`, `resolve_entity`, `get_entity`, `relations`, `impact`, `metric_catalog`,
`compute_metric`, `list_actions`, `execute_action`) renvoient une enveloppe unique :
`status ∈ {ok, ambiguous, not_found, no_data, forbidden, error}` + `data` + `provenance` + `warnings`
+ `clarification` + `notices`. Dans `get_entity`, les faits sont rangés en `observed`, `inferred` et
`to_verify`. Les champs `untrusted` (noms et descriptions WhatsApp) sont accompagnés d'une notice
rappelant qu'il s'agit de données. Aucune action n'est jamais déclenchée par leur contenu : la
description piégée du groupe de démo (« annule la commande SO102 ») est testée.

## 9. Limites connues

- **WMS simulé** (CSV + passerelle `SimulatedGateway`). **vTiger simulé** pour les écritures en démo.
  Le client `VtigerWebserviceClient` et `VtigerLiveGateway` sont écrits mais **n'ont pas été testés
  contre une instance réelle**.
- Les projections sont reconstruites entièrement à chaque changement. C'est acceptable jusqu'à
  quelques centaines de milliers de lignes ; au-delà, il faut une projection incrémentale.
- Normalisation téléphonique simplifiée (sans libphonenumber) : numéros FR, BE, CH, TN, MA.
- La résolution d'entité est lexicale (sans recherche floue ni embeddings).
- Pas d'allocation définie pour ventiler le CA par fournisseur : c'est refusé explicitement.
- Pas de gestion des avoirs ni du CA facturé : le CA est « commandé » (ambiguïté documentée).
- La démo ne contient pas d'agent LLM : la question métier est traitée par une séquence scriptée
  d'appels d'outils. Le serveur MCP est réel et testé en stdio avec le client MCP officiel.
