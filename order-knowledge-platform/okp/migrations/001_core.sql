-- Couche "core" : projection relationnelle courante, clés = identifiants canoniques.
-- Colonnes = propriétés du glossaire (test de cohérence : tests/test_models.py).
CREATE TABLE customer (
  customer_id TEXT PRIMARY KEY,
  name TEXT, email TEXT, phone TEXT, country TEXT, region TEXT, segment TEXT,
  status TEXT, marketing_consent INTEGER, note TEXT,
  updated_at TEXT NOT NULL, deleted_at TEXT
);
CREATE UNIQUE INDEX ux_customer_email ON customer(email) WHERE deleted_at IS NULL; -- C-CUSTOMER-EMAIL-UNIQUE

CREATE TABLE product (
  product_id TEXT PRIMARY KEY,
  sku TEXT NOT NULL, name TEXT, category TEXT, list_price_eur REAL, std_cost_eur REAL, active INTEGER,
  updated_at TEXT NOT NULL, deleted_at TEXT
);

CREATE TABLE warehouse (
  warehouse_id TEXT PRIMARY KEY,
  code TEXT NOT NULL, name TEXT, city TEXT, region TEXT, active INTEGER, capacity INTEGER,
  updated_at TEXT NOT NULL, deleted_at TEXT
);

CREATE TABLE supplier (
  supplier_id TEXT PRIMARY KEY,
  name TEXT, country TEXT, contact_email TEXT,
  updated_at TEXT NOT NULL, deleted_at TEXT
);

CREATE TABLE product_supplier (
  product_id TEXT NOT NULL REFERENCES product(product_id),
  supplier_id TEXT NOT NULL REFERENCES supplier(supplier_id),
  lead_time_days INTEGER, is_primary INTEGER,
  PRIMARY KEY (product_id, supplier_id)
);

CREATE TABLE sales_order (
  order_id TEXT PRIMARY KEY,
  erp_order_no TEXT NOT NULL,
  customer_id TEXT REFERENCES customer(customer_id),
  warehouse_id TEXT REFERENCES warehouse(warehouse_id),
  status TEXT NOT NULL, currency TEXT NOT NULL,
  ordered_at TEXT NOT NULL, order_date_local TEXT NOT NULL,
  confirmed_at TEXT, shipped_at TEXT, delivered_at TEXT, cancelled_at TEXT,
  cancel_reason TEXT, tracking_number TEXT,
  updated_at TEXT NOT NULL, deleted_at TEXT
);
CREATE INDEX ix_order_customer ON sales_order(customer_id);

CREATE TABLE order_line (
  line_id TEXT PRIMARY KEY,
  order_id TEXT REFERENCES sales_order(order_id),
  product_id TEXT REFERENCES product(product_id),
  line_no INTEGER NOT NULL, quantity INTEGER NOT NULL, unit_price REAL NOT NULL,
  discount_pct REAL NOT NULL, tax_rate REAL NOT NULL, unit_cost_eur REAL,
  returned_quantity INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL, deleted_at TEXT,
  CHECK (quantity >= 1), CHECK (returned_quantity >= 0 AND returned_quantity <= quantity)
);
CREATE INDEX ix_line_order ON order_line(order_id);

-- Données de référence non modélisées comme concepts (pas d'identité métier propre).
CREATE TABLE fx_rate (
  rate_date TEXT NOT NULL, currency TEXT NOT NULL, rate_to_eur REAL NOT NULL,
  PRIMARY KEY (rate_date, currency)
);
CREATE TABLE inventory_snapshot (
  snapshot_date TEXT NOT NULL,
  warehouse_id TEXT NOT NULL REFERENCES warehouse(warehouse_id),
  product_id TEXT NOT NULL REFERENCES product(product_id),
  on_hand INTEGER NOT NULL,
  PRIMARY KEY (snapshot_date, warehouse_id, product_id)
);
