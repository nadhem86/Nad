-- 002 : projections analytiques (modèle en étoile), clés = IRI canoniques du graphe.
-- Tables DÉRIVÉES : reconstruites par hub/projection.py, jamais modifiées à la main.

CREATE TABLE dim_client   (client_iri TEXT PRIMARY KEY, numero TEXT, raison_sociale TEXT, region TEXT, secteur TEXT);
CREATE TABLE dim_produit  (produit_iri TEXT PRIMARY KEY, reference TEXT, libelle TEXT, categorie TEXT);
CREATE TABLE dim_entrepot (entrepot_iri TEXT PRIMARY KEY, code TEXT, nom TEXT, region TEXT);
CREATE TABLE bridge_produit_fournisseur (produit_iri TEXT, fournisseur_iri TEXT, fournisseur_nom TEXT,
                                         PRIMARY KEY (produit_iri, fournisseur_iri));

CREATE TABLE fct_commande (
  commande_iri TEXT PRIMARY KEY, numero TEXT, client_iri TEXT, entrepot_iri TEXT, statut TEXT,
  date_commande_utc TEXT, date_locale TEXT, devise TEXT,
  confirmee_at TEXT, expediee_at TEXT, livree_at TEXT, annulee_at TEXT
);

CREATE TABLE fct_ligne (
  ligne_iri TEXT PRIMARY KEY, commande_iri TEXT, produit_iri TEXT, client_iri TEXT, entrepot_iri TEXT,
  statut TEXT, date_locale TEXT, devise TEXT,
  quantite REAL, quantite_retournee REAL, prix_unitaire REAL, remise_pct REAL, remise_montant REAL, taux_taxe REAL,
  taux_eur REAL,                 -- NULL si taux de change absent
  montant_net_devise REAL,       -- après remises et retours, HT, devise de la commande
  montant_net_eur REAL,          -- NULL si non convertible
  montant_manquant_raison TEXT,
  cout_unitaire_eur REAL,
  cout_eur REAL,                 -- NULL si coût inconnu
  cout_manquant_raison TEXT
);

CREATE TABLE fct_stock (
  releve_iri TEXT PRIMARY KEY, releve_date TEXT, produit_iri TEXT, entrepot_iri TEXT, quantite REAL
);

CREATE TABLE projection_state (id INTEGER PRIMARY KEY CHECK (id = 1), rebuilt_at TEXT, fact_watermark INTEGER);
