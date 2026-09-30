# Interface pour agents IA

Les agents accèdent à la plateforme par des **outils typés**, exposés de deux manières avec la même implémentation
(`okp/agent/tools.py`) :

- **HTTP** : `GET /agent/tools` (instructions + schémas JSON) et `POST /agent/tools/{tool}` avec `{"arguments": {...}}` ;
- **MCP** (SDK Python `mcp` v2, transport stdio) : `python -m okp mcp`.

## Outils

| Outil | Rôle | Principaux statuts |
|---|---|---|
| `get_definition(term)` | Définition versionnée d'un concept, d'une propriété, d'une relation, d'une métrique, d'une action ou d'un statut (synonymes, ambiguïtés, responsable) | ok · ambiguous · not_found |
| `resolve_entity(query, concept?)` | Identifiant canonique depuis un identifiant source ou un libellé | resolved · ambiguous · not_found |
| `get_entity(entity_id, include_history?)` | Propriétés avec provenance par valeur ; champs masqués listés | ok · not_found |
| `get_relations(entity_id, depth, include_inferred, predicates?, direction)` | Relations **observées** et **inférées** séparées | ok · not_found |
| `analyze_supplier_impact(supplier, open_orders_only)` | Commandes et clients dépendants d'un fournisseur | ok · ambiguous · not_found |
| `compute_metric(metric, group_by, filters, period_from, period_to)` | Calcul déterministe selon le modèle sémantique | ok · no_data · ambiguous · unknown_metric · forbidden |
| `list_available_actions(order_id)` | Pour chaque action : autorisation, transition, préconditions (sans effet) | ok · not_found |
| `execute_action(action, order_id, idempotency_key, …)` | Exécution contrôlée (ERP puis resynchronisation) | ok · forbidden · not_found · error |

Chaque réponse suit la même enveloppe : `{"tool", "status", "data", "error"?, "guidance"?}`.

## Garanties côté serveur

- **Identité et permissions** : pour MCP, la clé API est lue par le processus serveur (`OKP_API_KEY`) ; pour HTTP,
  elle vient de l'en-tête `X-API-Key`. Le canal `agent` est imposé par le point d'entrée : un paramètre d'outil ne
  peut pas le changer, et un paramètre inconnu est rejeté (`extra = forbid`).
- **Plafond du canal agent** (`models/policies.yaml#channels.agent`) : même un administrateur n'obtient ni données
  personnelles, ni annulation, ni lecture d'audit via un agent.
- **Aucune invention** : une définition absente renvoie `not_found`, un terme ambigu renvoie `ambiguous` avec les
  candidats, une métrique inconnue renvoie `unknown_metric`, et une période sans données renvoie `no_data` avec `value = null`
  (et non 0).
- **Provenance** : chaque valeur ou relation porte `provenance.kind` (`observed` ou `inferred`). Une valeur
  observée cite son système et son identifiant source, sa date de collecte et sa période de validité.
  Une valeur inférée cite `rule_id` et `derived_from`. Une métrique porte la version du modèle sémantique,
  la vue, les systèmes sources, la date des données, le SQL exécuté et ses paramètres.
- **Contenus non fiables** : les propriétés marquées `free_text` dans le glossaire sont renvoyées sous la forme
  `{"untrusted_text": …, "notice": …}`. Une instruction qu'elles contiendraient n'a aucun effet, car toute action
  exige une permission vérifiée côté serveur (voir `tests/test_agent.py::test_source_text_is_data_not_instruction`).
- **Journalisation** : chaque appel d'outil (`agent.tool_call`) et chaque décision sont écrits dans le journal
  d'audit chaîné.

Les instructions fournies aux agents (`AGENT_INSTRUCTIONS`) rappellent ces règles. Elles **complètent** les contrôles
du serveur, elles ne les remplacent pas.

## Brancher un client MCP

Exemple de configuration pour un client MCP en stdio (Claude Desktop, Claude Code, etc.). Adapter les chemins.
Cette configuration n'a pas été testée avec ces clients dans ce dépôt : seul le protocole a été testé, en mémoire
et en sous-processus stdio avec le client du SDK `mcp`.

```json
{
  "mcpServers": {
    "order-knowledge-platform": {
      "command": "python",
      "args": ["-m", "okp", "mcp"],
      "cwd": "/chemin/vers/order-knowledge-platform",
      "env": {
        "OKP_API_KEY": "demo-rep-idf-key",
        "OKP_DATA_DIR": "/chemin/vers/order-knowledge-platform/data"
      }
    }
  }
}
```

## Exemple de dialogue attendu

> *« Quelles commandes seraient touchées par un retard de Nordic Components ? »*

1. `resolve_entity("Nordic Components", "Supplier")` → `resolved`, `SUP-50AF960B6E`
2. `analyze_supplier_impact("SUP-50AF960B6E")` → produits (observés, SRM), commandes en cours (**inférées**
   par `R-SUPPLY-DEP`, avec les faits sources), clients
3. La réponse cite les sources et précise ce qui est indisponible (aucune date de livraison fournisseur n'est modélisée).

> *« Quelles sont les ventes de mars ? »* → `compute_metric("ventes")` renvoie `ambiguous` avec les candidats
> `order_count`, `quantity_sold` et `revenue`. L'agent doit alors demander laquelle est visée.

Le scénario `python -m okp demo` (étape 4) rejoue ces enchaînements avec un agent **scripté** (sans LLM).
