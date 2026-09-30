# Sécurité et gouvernance

## Contrôle d'accès (côté serveur, toutes interfaces)

Toutes les interfaces (API REST, outils agents HTTP, serveur MCP, CLI) passent par `okp/services.py`. Chaque
méthode reçoit un `Principal` authentifié et applique `okp/security/policy.py` :

| Contrôle | Mécanisme | Exemple de démo |
|---|---|---|
| Lecture d'entités | `entity.read:<Concept>` | L'opératrice entrepôt ne lit pas les Clients |
| Champs sensibles | `sensitivity` du glossaire + `field.read:pii` / `field.read:confidential` | L'analyste ne voit pas l'email, la commerciale ne voit pas le coût |
| Métriques | `metric.read:<nom>` + sensibilité de la métrique | La commerciale n'accède pas à la marge |
| Actions | `action.execute:<action>` | L'analyste ne confirme pas de commande |
| Périmètre métier (ABAC) | Attributs `regions`, `warehouses` du principal | Chloé (IDF) ne voit pas les commandes ARA ; Emma n'affecte qu'à LYS1 |
| Canal | `channels.<canal>.deny` | Via un agent : ni données personnelles, ni annulation, ni audit |

Le filtre de périmètre est **injecté** dans les requêtes de métriques et se cumule avec les filtres de l'appelant,
qui ne peuvent donc pas l'élargir. Si le filtre ne peut pas s'appliquer à une métrique (dimension non joignable),
l'accès est **refusé** plutôt que d'ignorer le filtre. Une entité hors périmètre renvoie `NOT_FOUND`, ce qui ne
divulgue pas son existence.

## Journal d'audit

- Table `audit_log` en **append-only** : des triggers SQL refusent UPDATE et DELETE.
- Chaque entrée est **chaînée par HMAC-SHA256** avec une clé secrète (`OKP_AUDIT_HMAC_KEY`) ; `verify()` détecte toute
  altération, y compris quand les triggers ont été contournés (testé).
- Événements journalisés : authentification refusée, lecture d'entité (avec les champs masqués), parcours de graphe,
  requête de métrique (filtres et filtres de sécurité), appel d'outil agent, action (demande rejouée, refus, échec,
  succès), ingestion et reprise de synchronisation. Chaque entrée porte les **versions des modèles** en vigueur.
- Minimisation : les détails ne contiennent jamais de valeur de champ personnel (testé).
- Le journal des modifications de **données** est le knowledge graph lui-même, puisque les faits ne sont jamais
  effacés (`superseded_at` / `retracted_at`, `valid_from` / `valid_to`). Le journal des **décisions** d'identité est
  constitué de `id_map` (règle et confiance) et de `ingestion_issue`.

## Secrets

- Les clés API ne sont jamais stockées en clair : seule l'empreinte SHA-256 est conservée, et la comparaison se fait
  à temps constant.
- `OKP_AUDIT_HMAC_KEY` est **obligatoire** en `OKP_ENV=prod` (le démarrage échoue sinon). En `dev`, une clé
  explicitement non sûre est utilisée.
- `.env` est exclu de Git et de l'image Docker. `.env.example` ne contient que des valeurs de démonstration.
- Production : remplacer les clés API par un fournisseur d'identité (OIDC) et stocker les secrets dans un coffre
  (Vault, KMS, secrets Kubernetes). Ce n'est pas implémenté ici.

## Données personnelles

- Chaque propriété est classée (`public`, `internal`, `confidential`, `pii`). Les données personnelles
  (email, téléphone, consentement, email fournisseur) sont masquées par défaut.
- Par politique, le canal agent ne reçoit jamais de données personnelles.
- Un email normalisé sert de clé de rapprochement. Il n'est jamais écrit dans l'audit.
- Non implémenté (à prévoir) : durées de conservation, purge ou pseudonymisation sur demande d'effacement
  (il faudrait rétracter **et** expurger les valeurs historiques des faits), et registre des traitements.

## Versionnement des modèles

- Chaque fichier `models/*.yaml` porte une version SemVer. L'empreinte de l'ensemble est exposée
  (`/health`, `fingerprint`) et figure dans chaque entrée d'audit et chaque résultat de métrique.
- `generated/*.ttl` sont générés à partir du glossaire. Un test échoue s'ils sont obsolètes.
- Le schéma de base évolue par migrations numérotées et immuables (`okp/migrations/`).
- Une modification de définition de métrique (formule, filtres) implique une version **mineure** au minimum, et une
  version **majeure** si les valeurs historiques changent de sens. Elle est décrite dans `models/CHANGELOG.md` avec
  son responsable métier.
