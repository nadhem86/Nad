"""Exactitude des métriques (oracle indépendant calculé sur les données brutes de l'ERP),
absence de double comptage, additivité et règles (fuseau, devises, valeurs manquantes)."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from okp.semantic.engine import MetricError

PARIS = ZoneInfo("Europe/Paris")


def _oracle(erp):
    """Recalcule CA, marge, quantités à partir des tables ERP brutes, sans passer par la plateforme."""
    rates = {}
    for r in erp.execute("SELECT RATE_DATE, CURRENCY, RATE_TO_EUR FROM ERP_FX_RATE"):
        rates[(r[0], r[1])] = r[2]

    def fx(cur, d):
        if cur == "EUR":
            return 1.0
        for back in range(0, 8):
            key = ((date.fromisoformat(d) - timedelta(days=back)).isoformat(), cur)
            if key in rates:
                return rates[key]
        return None

    out = {"revenue": 0.0, "margin_rev": 0.0, "margin": 0.0, "qty": 0, "orders": set(), "missing_fx": 0, "missing_cost": 0}
    for o in erp.execute("SELECT * FROM ERP_ORDER WHERE STATUS_CD NOT IN ('DRF','CAN')").fetchall():
        local = datetime.fromisoformat(o["ORDER_TS"].replace("Z", "+00:00")).astimezone(PARIS).date().isoformat()
        rate = fx(o["CURRENCY"], local)
        lines = erp.execute("SELECT * FROM ERP_ORDER_LINE WHERE ORD_NO=? AND QTY >= 1", (o["ORD_NO"],)).fetchall()
        if lines:
            out["orders"].add(o["ORD_NO"])
        for l in lines:
            net_qty = l["QTY"] - l["RETURNED_QTY"]
            out["qty"] += net_qty
            if rate is None:
                out["missing_fx"] += 1
                continue
            rev = net_qty * l["UNIT_PRICE"] * (1 - l["DISCOUNT_PCT"]) * rate
            out["revenue"] += rev
            if l["UNIT_COST"] is None:
                out["missing_cost"] += 1
                continue
            out["margin_rev"] += rev
            out["margin"] += rev - net_qty * l["UNIT_COST"]
    return out


def test_revenue_margin_quantity_match_independent_oracle(pf, who, erp):
    o = _oracle(erp)
    a = who("farid.analyst")
    rev = pf.query_metric(a, "revenue")
    assert rev["value"] == pytest.approx(o["revenue"], abs=0.01)
    assert {w["id"]: w["count"] for w in rev["warnings"]} == {"missing_fx": o["missing_fx"]}
    mar = pf.query_metric(a, "gross_margin")
    assert mar["value"] == pytest.approx(o["margin"], abs=0.01)
    assert {w["id"]: w["count"] for w in mar["warnings"]}["missing_cost"] == o["missing_cost"]
    rate = pf.query_metric(a, "gross_margin_rate")
    assert rate["value"] == pytest.approx(o["margin"] / o["margin_rev"], abs=1e-4)
    assert pf.query_metric(a, "quantity_sold")["value"] == o["qty"]
    assert pf.query_metric(a, "order_count")["value"] == len(o["orders"])


@pytest.mark.parametrize("metric", ["revenue", "gross_margin", "quantity_sold", "order_count"])
@pytest.mark.parametrize("axis", ["customer.region", "time.month", "status", "warehouse.code"])
def test_additive_metrics_do_not_double_count(pf, who, metric, axis):
    r = pf.query_metric(who("farid.analyst"), metric, [axis])
    assert sum(x["value"] for x in r["rows"]) == pytest.approx(r["value"], abs=0.01)


def test_line_metrics_by_product_do_not_double_count(pf, who):
    r = pf.query_metric(who("farid.analyst"), "revenue", ["product.category", "product.sku"])
    assert sum(x["value"] for x in r["rows"]) == pytest.approx(r["value"], abs=0.01)


def test_fan_out_dimension_is_rejected(pf, who):
    with pytest.raises(MetricError) as exc:
        pf.query_metric(who("farid.analyst"), "order_count", ["product.category"])
    assert exc.value.code == "FAN_OUT"
    assert "customer.region" in exc.value.details["allowed"]


def test_non_additive_ratio_is_recomputed_not_averaged(pf, who):
    r = pf.query_metric(who("farid.analyst"), "gross_margin_rate", ["customer.region"])
    num = sum(x["numerator"] for x in r["rows"])
    den = sum(x["denominator"] for x in r["rows"])
    assert r["value"] == pytest.approx(num / den, abs=1e-4)
    avg_of_rates = sum(x["value"] for x in r["rows"]) / len(r["rows"])
    assert abs(avg_of_rates - r["value"]) > 1e-6  # la moyenne des taux serait fausse


def test_average_order_value_consistent(pf, who):
    a = who("farid.analyst")
    aov = pf.query_metric(a, "average_order_value")
    assert aov["value"] == pytest.approx(aov["components"]["numerator"] / aov["components"]["denominator"], abs=1e-3)
    # Même périmètre que le CA : les commandes sans taux de change sont exclues des deux termes.
    assert aov["components"]["numerator"] == pytest.approx(pf.query_metric(a, "revenue")["value"], abs=0.01)
    assert {w["id"] for w in aov["warnings"]} == {"missing_fx"}


def test_timezone_rule_uses_europe_paris_local_date(pf, who):
    # SO-10001 : 2026-01-31 23:30 UTC → 2026-02-01 à Paris.
    r = pf.query_metric(who("farid.analyst"), "order_count", filters={"time.day": "2026-02-01"})
    ids = pf.conn.execute("SELECT erp_order_no FROM sales_order WHERE order_date_local='2026-02-01'").fetchall()
    assert "SO-10001" in [i[0] for i in ids]
    assert r["value"] >= 1
    jan31 = pf.conn.execute("SELECT COUNT(*) FROM sales_order WHERE erp_order_no='SO-10001' AND order_date_local='2026-01-31'").fetchone()[0]
    assert jan31 == 0


def test_cancellation_rate_definition(pf, who):
    r = pf.query_metric(who("farid.analyst"), "cancellation_rate")
    num = pf.conn.execute("SELECT COUNT(*) FROM sales_order WHERE status='cancelled' AND confirmed_at IS NOT NULL AND deleted_at IS NULL").fetchone()[0]
    den = pf.conn.execute("SELECT COUNT(*) FROM sales_order WHERE confirmed_at IS NOT NULL AND deleted_at IS NULL").fetchone()[0]
    assert r["components"] == {"numerator": num, "denominator": den}
    drafts_cancelled = pf.conn.execute("SELECT COUNT(*) FROM sales_order WHERE status='cancelled' AND confirmed_at IS NULL").fetchone()[0]
    assert drafts_cancelled >= 1  # présents dans les données mais exclus par définition


def test_processing_time_only_shipped_orders(pf, who):
    r = pf.query_metric(who("farid.analyst"), "processing_time_hours")
    rows = pf.conn.execute("SELECT (julianday(shipped_at)-julianday(confirmed_at))*24 FROM sales_order "
                           "WHERE status IN ('shipped','delivered') AND deleted_at IS NULL").fetchall()
    assert r["value"] == pytest.approx(sum(x[0] for x in rows) / len(rows), abs=1e-3)


def test_semi_additive_stock_uses_last_snapshot(pf, who):
    ops = who("emma.ops")
    r = pf.query_metric(ops, "stock_on_hand")
    last = pf.conn.execute("SELECT SUM(on_hand) FROM inventory_snapshot WHERE snapshot_date='2026-06-30'").fetchone()[0]
    both = pf.conn.execute("SELECT SUM(on_hand) FROM inventory_snapshot").fetchone()[0]
    assert r["value"] == last and r["value"] != both
    by_wh = pf.query_metric(ops, "stock_on_hand", ["warehouse.code"])
    assert sum(x["value"] for x in by_wh["rows"]) == last        # additif entre entrepôts
    june = pf.query_metric(ops, "stock_on_hand", period={"from": "2026-06-01", "to": "2026-06-15"})
    first = pf.conn.execute("SELECT SUM(on_hand) FROM inventory_snapshot WHERE snapshot_date='2026-06-01'").fetchone()[0]
    assert june["value"] == first


def test_period_without_data_returns_no_data_not_zero(pf, who):
    r = pf.query_metric(who("farid.analyst"), "revenue", period={"from": "2030-01-01", "to": "2030-12-31"})
    assert r["status"] == "no_data" and r["value"] is None


def test_invalid_inputs_rejected(pf, who):
    a = who("farid.analyst")
    for kwargs, code in (({"group_by": ["customer.email"]}, "INVALID_DIMENSION"),
                         ({"group_by": ["nope.x"]}, "INVALID_DIMENSION"),
                         ({"period": {"from": "2026-13-45"}}, "INVALID_PERIOD")):
        with pytest.raises(MetricError) as exc:
            pf.query_metric(a, "revenue", **kwargs)
        assert exc.value.code == code
    # Tentative d'injection SQL dans une valeur : paramètre lié, aucun effet.
    r = pf.query_metric(a, "revenue", filters={"customer.region": "IDF' OR '1'='1"})
    assert r["status"] == "no_data"


def test_metric_provenance(pf, who):
    r = pf.query_metric(who("farid.analyst"), "revenue")
    prov = r["provenance"]
    assert prov["kind"] == "computed" and prov["semantic_model_version"] == pf.registry.semantic.version
    assert prov["source_systems"] == ["erp"] and prov["data_as_of"] and prov["sql"].startswith("WITH base")
    assert r["definition"]["rules"]["taxes"].startswith("HT")
