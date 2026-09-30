-- 001 : knowledge graph (entités, identités sources, faits avec provenance), audit, idempotence.

CREATE TABLE entity (
  iri            TEXT PRIMARY KEY,               -- IRI canonique urn:nad:<slug>:<id>
  concept        TEXT NOT NULL,                  -- ex. Commande (classe nad:Commande)
  label          TEXT,
  created_at     TEXT NOT NULL,
  deleted_at     TEXT,                           -- suppression constatée dans la source (tombstone)
  deleted_reason TEXT
);
CREATE INDEX entity_concept ON entity(concept) WHERE deleted_at IS NULL;

-- Correspondance identifiants sources <-> IRI canonique (résolution d'identité).
CREATE TABLE source_identity (
  source       TEXT NOT NULL,                    -- vtiger | wms | whatsapp
  source_type  TEXT NOT NULL,                    -- module source (Accounts, SalesOrder, LineItem...)
  source_id    TEXT NOT NULL,                    -- identifiant dans la source (11x5, 1001, LIL, +336...)
  iri          TEXT NOT NULL REFERENCES entity(iri),
  first_seen   TEXT NOT NULL,
  last_seen    TEXT NOT NULL,
  PRIMARY KEY (source, source_type, source_id)
);
CREATE INDEX source_identity_iri ON source_identity(iri);

-- Un fait = (sujet, prédicat, objet) + provenance + validité métier + temps d'enregistrement.
CREATE TABLE fact (
  fact_id           INTEGER PRIMARY KEY AUTOINCREMENT,
  subject           TEXT NOT NULL REFERENCES entity(iri),
  predicate         TEXT NOT NULL,               -- nom local du glossaire (ex. passeePar)
  object_iri        TEXT,                        -- relation vers une entité
  object_value      TEXT,                        -- littéral sérialisé
  datatype          TEXT,                        -- string|decimal|integer|boolean|dateTime|date|enum|iri
  source            TEXT NOT NULL,               -- vtiger | wms | whatsapp | hub (inférence)
  source_record_id  TEXT,                        -- identifiant de l'enregistrement source
  method            TEXT NOT NULL CHECK (method IN ('import','saisie','inference')),
  rule_id           TEXT,                        -- règle d'inférence (R1, R2, R3)
  derived_from      TEXT,                        -- JSON : fact_id des prémisses
  confidence        REAL,
  status            TEXT NOT NULL CHECK (status IN ('valide','a_verifier','rejete')),
  collected_at      TEXT NOT NULL,               -- date de collecte dans la source
  valid_from        TEXT,                        -- période de validité métier
  valid_to          TEXT,
  recorded_at       TEXT NOT NULL,               -- temps transactionnel
  superseded_at     TEXT,                        -- NULL = fait courant
  superseded_reason TEXT,
  run_id            TEXT,
  actor             TEXT,
  CHECK ((object_iri IS NULL) <> (object_value IS NULL))
);
CREATE INDEX fact_current_sp ON fact(subject, predicate) WHERE superseded_at IS NULL;
CREATE INDEX fact_current_o  ON fact(object_iri, predicate) WHERE superseded_at IS NULL;
CREATE INDEX fact_rule       ON fact(rule_id) WHERE superseded_at IS NULL;

CREATE TABLE ingest_run (
  run_id       TEXT PRIMARY KEY,
  source       TEXT NOT NULL,
  mode         TEXT NOT NULL,                    -- snapshot | delta | partial
  input_path   TEXT,
  input_sha256 TEXT,
  started_at   TEXT NOT NULL,
  finished_at  TEXT,
  stats        TEXT
);

-- Journal d'audit chaîné (hash de l'entrée précédente) : toute altération est détectable.
CREATE TABLE audit_log (
  seq        INTEGER PRIMARY KEY AUTOINCREMENT,
  ts         TEXT NOT NULL,
  actor      TEXT NOT NULL,
  roles      TEXT,
  kind       TEXT NOT NULL,                      -- read | action | denied | ingest | desync
  action     TEXT NOT NULL,
  target     TEXT,
  request    TEXT,
  outcome    TEXT NOT NULL,
  detail     TEXT,
  prev_hash  TEXT NOT NULL,
  hash       TEXT NOT NULL
);

CREATE TABLE action_request (
  idempotency_key TEXT PRIMARY KEY,
  actor           TEXT NOT NULL,
  action          TEXT NOT NULL,
  target          TEXT NOT NULL,
  request_hash    TEXT NOT NULL,
  response        TEXT NOT NULL,
  created_at      TEXT NOT NULL
);

-- Données de référence (hors graphe) : taux de change.
CREATE TABLE fx_rate (
  currency    TEXT NOT NULL,
  day         TEXT NOT NULL,
  rate_to_eur REAL NOT NULL,
  source      TEXT NOT NULL,
  PRIMARY KEY (currency, day)
);

-- Écritures envoyées aux passerelles SIMULÉES (démo). Une passerelle réelle écrit dans la source.
CREATE TABLE sim_source_write (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  system    TEXT NOT NULL,
  module    TEXT NOT NULL,
  record_id TEXT NOT NULL,
  patch     TEXT NOT NULL,
  ts        TEXT NOT NULL
);
