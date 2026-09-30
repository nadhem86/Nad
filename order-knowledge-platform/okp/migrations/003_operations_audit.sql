-- Idempotence des actions.
CREATE TABLE action_log (
  idempotency_key TEXT PRIMARY KEY,
  action TEXT NOT NULL,
  principal_id TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  outcome TEXT NOT NULL,             -- succeeded | failed
  response TEXT NOT NULL,            -- JSON rejoué à l'identique
  created_at TEXT NOT NULL
);

-- File de resynchronisation lorsqu'une action a réussi dans la source mais que la
-- mise à jour des représentations dérivées a échoué (reprise : `okp sync`).
CREATE TABLE sync_outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL,
  processed_at TEXT,
  error TEXT
);

-- Journal d'audit append-only, chaîné par HMAC (détection d'altération).
CREATE TABLE audit_log (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  principal_id TEXT NOT NULL,
  channel TEXT NOT NULL,
  event_type TEXT NOT NULL,
  resource TEXT,
  decision TEXT NOT NULL,            -- allow | deny | success | failure | info
  details TEXT NOT NULL,
  model_versions TEXT NOT NULL,
  prev_hash TEXT NOT NULL,
  hash TEXT NOT NULL
);
CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_log BEGIN SELECT RAISE(ABORT, 'audit_log est append-only'); END;
CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_log BEGIN SELECT RAISE(ABORT, 'audit_log est append-only'); END;
