# Journal des modèles métier

Format : SemVer par fichier. MAJEURE = changement de sens (valeurs historiques non comparables) ;
MINEURE = ajout compatible (concept, métrique, action) ; CORRECTIF = libellé, documentation.

## 2026-09-30 — première version

| Modèle | Version | Contenu | Responsable |
|---|---|---|---|
| glossary.yaml | 1.0.0 | Client, Commande, LigneCommande, Produit, Entrepôt, Fournisseur ; 5 relations ; 3 relations inférées ; 8 contraintes | ADV, Achats, Logistique |
| semantic_model.yaml | 1.0.0 | 5 dimensions, 3 faits, 9 métriques (dont marge, panier moyen, délai, taux d'annulation, stock semi-additif) | Direction financière, Direction commerciale |
| operations.yaml | 1.0.0 | 6 états, 7 transitions, 5 actions | ADV |
| policies.yaml | 1.0.0 | 6 rôles, plafonds par canal (api, cli, agent) | RSSI / DPO |
