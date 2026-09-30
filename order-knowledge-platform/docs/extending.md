# Étendre la plateforme

Règle générale : on modifie d'abord le **modèle versionné** (`models/*.yaml`), on incrémente sa `version`, on
décrit le changement dans `models/CHANGELOG.md`, puis on adapte le code si nécessaire. `python -m pytest`
vérifie la cohérence entre couches (colonnes core, vues, préconditions, permissions, ontologie générée).

## Ajouter une entité (exemple : `Carrier` — transporteur)

1. **Glossaire** (`models/glossary.yaml`) : ajouter le concept avec `label`, `table`, `id_prefix` unique,
   `owner`, `definition`, `synonyms`, `ambiguities`, `identity.anchor` (système + entité source qui produit
   l'identifiant), `source_mappings` (clé source et `authority_for`) et `properties` (type, obligatoire,
   sensibilité, bornes).
   Ajouter les relations (`relations.shippedBy: {domain: Order, range: Carrier, min: 0, max: 1, projection: …}`)
   et les éventuelles contraintes (`constraints`, avec requête SPARQL si la règle est conditionnelle).
2. **Migration** : créer `okp/migrations/005_carrier.sql`, qui contient la table core (colonne `carrier_id` + une colonne
   par propriété + `updated_at`, `deleted_at`) et la colonne de projection de la relation (`sales_order.carrier_id`).
   Les migrations appliquées ne sont jamais modifiées.
3. **Ingestion** (`okp/ingestion/sources.py`) :
   - un adaptateur d'extraction ;
   - un cas dans `map_record` qui renvoie un `MappedRecord` (propriétés du glossaire et `RelationAssertion`) ;
   - l'insertion dans `PROCESSING_ORDER`, avant les entités qui le référencent ;
   - le nom de la colonne d'identifiant dans `_ID_COLUMNS` (`pipeline.py`).
4. **Ontologie** : `python -m okp build-ontology` régénère `generated/*.ttl`, qui sont à committer.
5. **Politique** : ajouter `entity.read:Carrier` aux rôles concernés dans `models/policies.yaml`.
6. **Tests** : ajouter des cas de démo dans `okp/demo/generate.py` et des tests ciblés (validation, identité,
   suppression). Il faut aussi ajouter `Carrier` aux concepts de `ResolveEntityIn` (`okp/agent/tools.py`).

## Ajouter une métrique (exemple : taux de retour)

Dans `models/semantic_model.yaml` :

```yaml
  return_rate:
    label: Taux de retour
    synonyms: [taux de retour, retours]
    owner: "Direction commerciale"
    description: "Quantités retournées / quantités commandées, hors brouillons et annulées."
    fact: order_line                       # fait dont le grain convient
    type: ratio                            # sum | count_distinct | ratio | last_snapshot_sum
    numerator: "SUM(returned_quantity)"
    denominator: "SUM(quantity)"
    filters: ["order_status NOT IN ('draft', 'cancelled')"]
    unit: ratio
    additivity: non_additive
    sensitivity: internal
    rules:
      returns: "Rattachés à la période de la commande."
      missing_values: "Dénominateur nul → null."
```

Points de vigilance :

- Choisir le **fait** dont le grain évite le fan-out. Une mesure au niveau commande qui a besoin du produit n'est pas
  valide : il faut la définir au grain ligne.
- Pour un ratio, exprimer numérateur et dénominateur en **sommes** : le moteur les recalcule à chaque niveau
  d'agrégation.
- Déclarer des `quality_checks` pour chaque exclusion (valeurs manquantes) : elles remontent dans `warnings`.
- Donner l'accès dans `models/policies.yaml` (`metric.read:return_rate`) si le rôle n'a pas `metric.read:*`.
- Si une colonne nouvelle est nécessaire, l'ajouter à la vue par une **nouvelle** migration (`DROP VIEW` puis `CREATE VIEW`).
- Écrire un test qui compare la valeur à un calcul indépendant sur les données brutes (voir `tests/test_metrics.py`).

## Ajouter une action (exemple : `hold_order` — mise en attente)

1. **États et transitions** (`models/operations.yaml`) : ajouter l'état `on_hold` (code ERP), les transitions
   `confirmed → on_hold` et `on_hold → confirmed`, et **ajouter la valeur à `Order.status` dans le glossaire**
   (le registre refuse le chargement si les deux listes divergent).
2. **Contrat** : sous `actions.hold_order`, renseigner `permission`, `input` (types, obligatoires, `pattern`,
   `min_length`), `preconditions`, `effects`, `errors`, `idempotency` et `audit`. Le modèle d'entrée Pydantic est
   généré automatiquement à partir de `input`.
3. **Préconditions** : les écrire dans `okp/operations/preconditions.py`, sous la forme
   `(conn, ctx, params) → (bool, explication)`, puis les enregistrer dans `REGISTRY`.
4. **Effets dans la source** : ajouter l'entrée `ERP_EFFECTS["hold_order"]` (`okp/operations/actions.py`) et, si
   besoin, autoriser les champs correspondants dans `ErpGateway.transition`.
5. **Politique** : ajouter `action.execute:hold_order` aux rôles concernés. Si l'action ne doit pas être exécutée
   par un agent, l'ajouter à `channels.agent.deny`.
6. **Agents** : ajouter le nom à la `Literal` d'`ExecuteActionIn` (`okp/agent/tools.py`) et à `ActionName` (`mcp_server.py`).
7. **Tests** : couvrir la transition valide, une transition invalide, chaque précondition, le refus par rôle et
   l'idempotence.
