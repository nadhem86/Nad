-- Knowledge graph : entités canoniques, correspondances d'identifiants et faits avec provenance.
CREATE TABLE entity (
  canonical_id TEXT PRIMARY KEY,
  concept TEXT NOT NULL,
  label TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT
);

-- Résolution d'identité : (système, entité source, id source) -> identifiant canonique.
CREATE TABLE id_map (
  source_system TEXT NOT NULL,
  source_entity TEXT NOT NULL,
  source_id TEXT NOT NULL,
  canonical_id TEXT NOT NULL REFERENCES entity(canonical_id),
  match_rule TEXT NOT NULL,          -- ANCHOR | ID-EMAIL | ...
  confidence REAL NOT NULL,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  PRIMARY KEY (source_system, source_entity, source_id)
);
CREATE INDEX ix_id_map_canonical ON id_map(canonical_id);

-- Un fait = (sujet, prédicat, objet) + qualificatifs + provenance + temporalité.
-- Courant  <=> superseded_at IS NULL AND retracted_at IS NULL.
CREATE TABLE fact (
  fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
  subject TEXT NOT NULL,
  predicate TEXT NOT NULL,
  object_id TEXT,                     -- relation vers une entité
  object_value TEXT,                  -- littéral (JSON)
  qualifiers TEXT NOT NULL DEFAULT '{}',
  source_system TEXT NOT NULL,        -- erp | crm | wms | srm | okp (inférence/action)
  source_entity TEXT NOT NULL,
  source_id TEXT NOT NULL,            -- identifiant de l'enregistrement source (ou de la règle)
  method TEXT NOT NULL CHECK (method IN ('import', 'manual', 'inference')),
  rule_id TEXT,                       -- obligatoire si method = inference
  derived_from TEXT,                  -- JSON: fact_id sources d'une inférence
  validation_status TEXT NOT NULL CHECK (validation_status IN ('validated', 'pending_review', 'rejected')),
  collected_at TEXT NOT NULL,         -- date de collecte (exécution d'ingestion)
  valid_from TEXT,                    -- période de validité métier
  valid_to TEXT,
  run_id TEXT,
  superseded_at TEXT,
  retracted_at TEXT,
  CHECK ((object_id IS NULL) <> (object_value IS NULL)),
  CHECK (method <> 'inference' OR (rule_id IS NOT NULL AND derived_from IS NOT NULL))
);
CREATE INDEX ix_fact_subject ON fact(subject) WHERE superseded_at IS NULL AND retracted_at IS NULL;
CREATE INDEX ix_fact_object ON fact(object_id) WHERE superseded_at IS NULL AND retracted_at IS NULL;
CREATE INDEX ix_fact_record ON fact(source_system, source_entity, source_id);

-- État de chaque enregistrement source vu (idempotence et détection des suppressions).
CREATE TABLE source_record (
  source_system TEXT NOT NULL,
  source_entity TEXT NOT NULL,
  source_id TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  canonical_id TEXT,
  last_run_id TEXT NOT NULL,
  seen_at TEXT NOT NULL,
  deleted_at TEXT,
  PRIMARY KEY (source_system, source_entity, source_id)
);

CREATE TABLE ingestion_run (
  run_id TEXT PRIMARY KEY,
  mode TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  stats TEXT
);

-- Quarantaine : enregistrements rejetés (validation, référence non résolue, doublon...).
CREATE TABLE ingestion_issue (
  issue_id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  source_system TEXT NOT NULL,
  source_entity TEXT NOT NULL,
  source_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  detail TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE sync_cursor (
  source_system TEXT PRIMARY KEY,
  position INTEGER NOT NULL,
  updated_at TEXT NOT NULL
);
