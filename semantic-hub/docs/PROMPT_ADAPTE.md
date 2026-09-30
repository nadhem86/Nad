# Prompt adapté au contexte du dépôt `nadhem86/Nad`

Ce fichier conserve la version du prompt réellement exécutée. Elle reprend le prompt générique
d'origine et remplace les champs `[à compléter]` par ce que l'inspection du dépôt a permis d'établir.
Les hypothèses sont signalées par **(H)** et restent à confirmer par le métier.

---

Agis comme un architecte logiciel spécialisé dans la modélisation métier, les modèles sémantiques,
les knowledge graphs, les ontologies et les agents IA.

Ta mission : concevoir puis implémenter une infrastructure métier réutilisable (`semantic-hub/`)
qui complète l'extension Chrome existante **WhatsApp Web → vTiger CRM** (`whatsapp-extractor/`).
Cette infrastructure doit servir aux dashboards, aux applications (dont l'extension), aux workflows
et aux agents IA. Livre une implémentation qui fonctionne : code, données fictives, tests et documentation.

## 1. Contexte et périmètre (constaté dans le dépôt)

| Élément | Valeur retenue |
|---|---|
| Domaine métier | Gestion commerciale d'une PME qui vend via vTiger CRM et prospecte/échange avec ses clients sur WhatsApp. Périmètre : clients, contacts, commandes, lignes, produits, entrepôts, fournisseurs, identités WhatsApp. |
| Sources de données | **vTiger CRM** (webservice REST `webservice.php` : modules `Accounts`, `Contacts`, `Products`, `Vendors`, `SalesOrder`), qui fait autorité sur les données commerciales. **Export JSON de l'extension** (`whatsapp-export.json` : `contacts`, `groups`, `conversations`). **WMS / logistique (H)** : aucun WMS n'existe dans le dépôt, il est simulé par des fichiers CSV. **Taux de change de référence (H)** : fichier CSV. |
| Environnement cible | Démonstration locale reproductible. La cible **(H)** est une infrastructure souveraine ou un cloud privé, sans dépendance à un service SaaS tiers. |
| Stack existante | JavaScript (extension Chrome MV3) et vTiger CRM (PHP/MySQL, hors dépôt). Aucune stack backend : Python 3.11 + SQLite est proposé pour rester léger. |
| Consommateurs | Extension Chrome (popup), tableaux de bord vTiger ou BI via API, workflows (n8n ou équivalent **(H)**) et agents IA via MCP. |
| Contraintes | Données personnelles issues de WhatsApp (RGPD : minimisation obligatoire, pas de contenu de messages). Secrets vTiger (clé d'accès) jamais versionnés. Volumétrie PME (**(H)** < 1 M lignes). Budget minimal : pas de base graphe dédiée. |

À défaut de données réelles, construis une démonstration locale avec des données fictives au format
vTiger et au format de l'export de l'extension.

## 2 à 11. Exigences

Les exigences des sections 2 à 11 du prompt d'origine sont reprises telles quelles : distinction des
quatre couches, glossaire versionné, knowledge graph avec provenance, modèle sémantique analytique,
couche opérationnelle, sécurité et gouvernance, interface agents (API typée + MCP), architecture
proportionnée, tests d'acceptation et démonstration en 6 étapes. Elles sont précisées ainsi :

- Le **cycle de vie des commandes** (brouillon, confirmée, en préparation, expédiée, livrée, annulée)
  se répartit entre deux autorités. vTiger porte `sostatus` (Created, Approved, Cancelled, Delivered).
  Le WMS **(H)** porte les étapes préparation, expédition et livraison.
- **Résolution d'identité** entre un numéro WhatsApp et un Contact vTiger, à partir du téléphone
  normalisé E.164. C'est un fait **inféré**, traçable, qui peut être ambigu.
- Tout texte venant de WhatsApp (nom, description de groupe) est une **donnée non fiable** qui ne
  doit jamais déclencher d'action.
- Les contenus de messages WhatsApp (`lastMessage`) et les statuts « à propos » ne sont **pas ingérés**
  (minimisation).
