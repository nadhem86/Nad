-- Vues du modèle sémantique. Grain documenté dans models/semantic_model.yaml.
CREATE VIEW v_dim_customer AS
  SELECT customer_id, name, segment, region, country FROM customer WHERE deleted_at IS NULL;
CREATE VIEW v_dim_product AS
  SELECT product_id, sku, name, category FROM product WHERE deleted_at IS NULL;
CREATE VIEW v_dim_warehouse AS
  SELECT warehouse_id, code, name, region FROM warehouse WHERE deleted_at IS NULL;

-- Grain : une ligne de commande. Taux de change : jour de commande, sinon dernier taux
-- des 7 jours précédents ; NULL sinon (la ligne sera exclue et signalée).
CREATE VIEW v_fact_order_line AS
WITH base AS (
  SELECT l.line_id, l.order_id, l.line_no, o.customer_id, l.product_id, o.warehouse_id,
         o.status AS order_status, o.order_date_local, o.currency,
         l.quantity, l.returned_quantity, (l.quantity - l.returned_quantity) AS net_quantity,
         l.unit_price, l.discount_pct, l.tax_rate, l.unit_cost_eur,
         CASE WHEN o.currency = 'EUR' THEN 1.0 ELSE (
           SELECT r.rate_to_eur FROM fx_rate r
           WHERE r.currency = o.currency
             AND r.rate_date <= o.order_date_local
             AND r.rate_date >= date(o.order_date_local, '-7 day')
           ORDER BY r.rate_date DESC LIMIT 1) END AS fx_rate_to_eur,
         CASE WHEN o.confirmed_at IS NOT NULL THEN 1 ELSE 0 END AS ever_confirmed
  FROM order_line l
  JOIN sales_order o ON o.order_id = l.order_id
  WHERE l.deleted_at IS NULL AND o.deleted_at IS NULL
)
SELECT *,
  CASE WHEN fx_rate_to_eur IS NULL THEN 0 ELSE 1 END AS has_fx,
  CASE WHEN unit_cost_eur IS NULL THEN 0 ELSE 1 END AS has_cost,
  quantity * unit_price * fx_rate_to_eur AS gross_amount_eur,
  net_quantity * unit_price * (1 - discount_pct) * fx_rate_to_eur AS net_revenue_eur,
  net_quantity * unit_price * (1 - discount_pct) * tax_rate * fx_rate_to_eur AS tax_amount_eur,
  net_quantity * unit_cost_eur AS cogs_eur
FROM base;

-- Grain : une commande. Montants pré-agrégés depuis les lignes (évite le fan-out).
CREATE VIEW v_fact_order AS
SELECT o.order_id, o.customer_id, o.warehouse_id, o.status AS order_status, o.order_date_local,
       o.currency,
       CASE WHEN o.confirmed_at IS NOT NULL THEN 1 ELSE 0 END AS ever_confirmed,
       (SELECT COUNT(*) FROM v_fact_order_line f WHERE f.order_id = o.order_id) AS line_count,
       (SELECT SUM(f.net_revenue_eur) FROM v_fact_order_line f WHERE f.order_id = o.order_id) AS net_revenue_eur,
       CASE WHEN EXISTS (SELECT 1 FROM v_fact_order_line f WHERE f.order_id = o.order_id AND f.has_fx = 0)
            THEN 0 ELSE 1 END AS has_fx,
       CASE WHEN o.shipped_at IS NOT NULL AND o.confirmed_at IS NOT NULL
            THEN (julianday(o.shipped_at) - julianday(o.confirmed_at)) * 24.0 END AS processing_hours
FROM sales_order o
WHERE o.deleted_at IS NULL;

CREATE VIEW v_fact_inventory AS
SELECT s.snapshot_date, s.warehouse_id, s.product_id, s.on_hand
FROM inventory_snapshot s
JOIN warehouse w ON w.warehouse_id = s.warehouse_id AND w.deleted_at IS NULL
JOIN product p ON p.product_id = s.product_id AND p.deleted_at IS NULL;
