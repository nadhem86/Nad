# Étendre le hub

Chaque extension suit le même principe : **déclarer d'abord** (YAML versionné), **implémenter
ensuite** (Python), puis **tester**. Pensez à incrémenter la `version` du fichier modifié et à
compléter son `changelog` s'il en a un.

## Ajouter une entité (concept)

Exemple : `Facture`, rattachée à une Commande.

1. `ontology/glossary.yaml`, sous `concepts:`

   ```yaml
   Facture:
     slug: facture
     label: Facture
     definition: "Document comptable émis pour une Commande (module vTiger Invoice)."
     owner: Direction financière
     synonyms: [Invoice]
     source_ids:
       - {source: vtiger, module: Invoice, id_field: id, business_key: invoice_no}
     properties:
       numeroFacture: {type: string, required: true}
       montantHT:     {type: decimal, required: true, min_inclusive: 0}
     relations:
       facturePour:   {target: Commande, min: 1, max: 1}
   ```
2. `python scripts/build_ontology.py` régénère `ontology.ttl` et `shapes.ttl`. Le test
   `test_generated_ttl_in_sync_with_glossary` échoue tant que ce n'est pas fait.
3. Ingestion : dans `hub/ingest/vtiger.py`, ajouter `"Invoice": "Facture"` à `MODULE_CONCEPT`, le
   module à `MODULE_ORDER` et une méthode `_map_Invoice(self, r) -> (values, label)`.
   `upsert_facts` refuse tout prédicat absent du glossaire.
4. Sécurité : ajouter `entity:read:Facture` aux rôles concernés dans `security/policies.yaml`.
   Si le concept dépend d'un client, l'ajouter à `PolicyEngine.REGION_SCOPED` et à `client_region`.
5. Agents : ajouter les clés de recherche dans `SEARCH_KEYS` (`hub/agent_tools.py`) et, si besoin,
   un chemin dans `impact_paths`.
6. Tests : validité SHACL, idempotence du réimport et visibilité par rôle.

## Ajouter une métrique

Exemple : `taux_retour` = quantités retournées / quantités commandées.

1. `semantic_model/model.yaml` :

   ```yaml
   taux_retour:
     label: Taux de retour
     definition: "Quantités retournées / quantités commandées, sur les commandes livrées."
     owner: Direction logistique
     table: fct_ligne
     expression: "SUM(COALESCE(fct_ligne.quantite_retournee,0)) / NULLIF(SUM(fct_ligne.quantite),0)"
     filters: ["fct_ligne.statut = 'livree'"]
     additivity: non_additive
     unit: ratio
     rules: [retours, manquants]
   ```
   Si la métrique a besoin d'une nouvelle colonne, l'ajouter par une **nouvelle migration**
   (`hub/migrations/003_*.sql`) et la calculer dans `hub/projection.py`. Ne jamais modifier une
   migration déjà appliquée.
2. Donner le droit `metric:taux_retour` aux rôles concernés (`security/policies.yaml`).
3. Ajouter un test qui compare la valeur à un calcul indépendant (`tests/oracle.py`).
4. La métrique est automatiquement exposée par `/metrics`, `/metrics/query` et les outils MCP
   `metric_catalog` et `compute_metric`.

Règles : une métrique ne porte que sur **une** table de faits. Une dimension N-N est refusée tant
qu'aucune règle d'allocation n'est définie. Un ratio se calcule à partir de sommes, jamais comme une
moyenne de ratios.

## Ajouter une action

Exemple : `reprogrammer_livraison`.

1. `operations/order_lifecycle.yaml` : déclarer l'action (autorité, permission, `agent_allowed`,
   contrat d'entrée et de sortie, préconditions, validations, effets, erreurs) et la ou les
   transitions (`from` / `to` / `action`, ou une transition réflexive si l'état ne change pas).
2. `hub/operations.py` : écrire `_prepare_reprogrammer_livraison(self, principal, order, params)`.
   Elle valide et renvoie `(faits_kg, patch_source, module_source, id_source)`. Idempotence, droits,
   périmètre, transition, écriture dans la source d'abord, inférences, projections et audit sont gérés
   par `execute()` : il n'y a rien à réécrire.
3. Si la source est nouvelle, ajouter une passerelle dans `hub/gateways.py` et l'enregistrer dans
   `Hub._gateways()`. L'ingestion correspondante doit relire ce qu'elle a écrit.
4. Donner `action:reprogrammer_livraison` aux rôles concernés.
5. Tests : transition valide, transition invalide, refus pour un rôle ou un agent, rejeu idempotent,
   panne de la source.
