"""Exactitude des métriques (oracle indépendant), doubles comptages, additivité, fuseau, devises, manquants."""

import pytest

from hub.ingest.pipeline import ingest
from hub.semantic import MetricError, MetricQuery
from tests import oracle

Q3 = dict(period_start="2026-07-01", period_end="2026-09-30")


def val(hub, metric, **kw):
    r = hub.metrics.run(MetricQuery(metrics=[metric], **kw))[metric]
    return r["rows"][0]["value"] if r["rows"] else None


def test_ca_marge_nb_match_independent_oracle(hub):
    assert val(hub, "chiffre_affaires", **Q3) == pytest.approx(oracle.ca(start="2026-07-01", end="2026-09-30"))
    assert val(hub, "marge", **Q3) == pytest.approx(oracle.marge(start="2026-07-01", end="2026-09-30"))
    assert val(hub, "nb_commandes", **Q3) == oracle.nb_commandes(start="2026-07-01", end="2026-09-30")
    assert val(hub, "chiffre_affaires", **Q3) == pytest.approx(4070.4)
    assert val(hub, "marge", **Q3) == pytest.approx(1456.4)


def test_ratios_computed_from_sums_not_averaged(hub):
    ca, m = val(hub, "chiffre_affaires"), val(hub, "marge")
    ca_costed = hub.conn.execute("SELECT SUM(montant_net_eur) FROM fct_ligne WHERE cout_eur IS NOT NULL AND montant_net_eur "
                                 "IS NOT NULL AND statut IN ('confirmee','en_preparation','expediee','livree')").fetchone()[0]
    assert val(hub, "taux_marge") == pytest.approx(m / ca_costed)
    assert val(hub, "panier_moyen") == pytest.approx(ca / 6)  # SO109 (GBP) exclu : 6 commandes valorisées
    by_client = hub.metrics.run(MetricQuery(metrics=["taux_marge"], group_by=["client.nom"]))["taux_marge"]["rows"]
    assert val(hub, "taux_marge") != pytest.approx(sum(r["value"] for r in by_client) / len(by_client))


def test_additive_metric_sums_across_dimension(hub):
    total = val(hub, "chiffre_affaires")
    rows = hub.metrics.run(MetricQuery(metrics=["chiffre_affaires"], group_by=["produit.reference"]))["chiffre_affaires"]["rows"]
    assert sum(r["value"] for r in rows) == pytest.approx(total)


def test_no_double_counting_with_many_to_many_supplier(hub):
    naive = hub.conn.execute(
        "SELECT SUM(l.montant_net_eur) FROM fct_ligne l JOIN bridge_produit_fournisseur b ON b.produit_iri=l.produit_iri "
        "WHERE l.statut IN ('confirmee','en_preparation','expediee','livree')").fetchone()[0]
    assert naive > val(hub, "chiffre_affaires")  # la jointure naïve double-compte PRO5 (2 fournisseurs)
    with pytest.raises(MetricError) as e:
        hub.metrics.compile("chiffre_affaires", MetricQuery(metrics=["chiffre_affaires"], group_by=["fournisseur.nom"]))
    assert e.value.code == "FAN_OUT"


def test_forbidden_dimensions(hub):
    for metric, dim in (("nb_commandes", "produit.reference"), ("panier_moyen", "produit.reference"),
                        ("stock_disponible", "temps.mois")):
        with pytest.raises(MetricError):
            hub.metrics.compile(metric, MetricQuery(metrics=[metric], group_by=[dim]))


def test_timezone_assigns_order_to_local_day(hub):
    # SO103 : 2026-06-30 22:30 UTC = 1er juillet 00:30 à Paris -> T3
    row = hub.conn.execute("SELECT date_commande_utc, date_locale FROM fct_commande WHERE numero='SO103'").fetchone()
    assert row["date_commande_utc"].startswith("2026-06-30") and row["date_locale"] == "2026-07-01"
    q2 = val(hub, "chiffre_affaires", period_start="2026-04-01", period_end="2026-06-30")
    assert q2 is None


def test_currency_conversion_and_missing_rate(hub):
    r = hub.metrics.run(MetricQuery(metrics=["chiffre_affaires"], filters={"client.nom": "Hôtel Lumière"}))["chiffre_affaires"]
    assert r["rows"][0]["value"] == pytest.approx(1970 * 0.92)  # SO104 en USD ; SO109 en GBP exclue
    assert {"excluded_rows": 1, "column": "montant_net_eur", "reason": "taux_change_absent:GBP:2026-09-20"} in r["warnings"]


def test_missing_cost_excluded_from_margin_not_zeroed(hub):
    r = hub.metrics.run(MetricQuery(metrics=["marge"], filters={"client.nom": "Café du Port"}, **Q3))["marge"]
    assert r["rows"][0]["value"] == pytest.approx(30 * 24 * 0.95 - 30 * 14)
    assert any(w["reason"] == "cout_unitaire_inconnu" for w in r["warnings"])


def test_cancelled_drafts_and_returns_rules(hub):
    ca_cancelled = hub.metrics.run(MetricQuery(metrics=["chiffre_affaires"], filters={"statut.code": "annulee"}))
    assert ca_cancelled["chiffre_affaires"]["status"] == "no_data"
    assert val(hub, "taux_annulation") == pytest.approx(2 / 9)
    assert val(hub, "quantite_vendue", filters={"client.nom": "Hôtel Lumière"}) == 50 - 5 + 40 + 10


def test_processing_delay_and_semi_additive_stock(hub):
    assert val(hub, "delai_traitement") == pytest.approx((30 + 44 + 46 + 22) / 4)
    assert val(hub, "stock_disponible", period_start="2026-09-01", period_end="2026-09-30") == 321
    assert val(hub, "stock_disponible", period_start="2026-09-29", period_end="2026-09-29") == 364
    naive = hub.conn.execute("SELECT SUM(quantite) FROM fct_stock").fetchone()[0]
    assert naive == 321 + 364  # l'addition dans le temps serait fausse


def test_source_change_propagates_to_metrics(hub):
    before = val(hub, "chiffre_affaires", filters={"client.nom": "Boulangerie Martin"})
    ingest(hub, "vtiger", hub.settings.demo_dir / "vtiger" / "delta_001_2026-09-30.json")
    after = val(hub, "chiffre_affaires", filters={"client.nom": "Boulangerie Martin"})
    assert after - before == pytest.approx(45.0)
    assert val(hub, "chiffre_affaires") == pytest.approx(oracle.ca(delta="delta_001_2026-09-30.json"))
    assert val(hub, "marge") == pytest.approx(oracle.marge(delta="delta_001_2026-09-30.json"))
