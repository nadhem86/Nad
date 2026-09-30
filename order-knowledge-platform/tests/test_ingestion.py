"""Ingestion : idempotence, résolution d'identité, quarantaine, suppressions, propagation."""

import csv
import json

from conftest import canonical, current_facts, make_settings

from okp.demo.generate import generate
from okp.services import Platform


def _count(pf, sql, *args):
    return pf.conn.execute(sql, args).fetchone()[0]


def test_initial_import_counts(pf):
    assert _count(pf, "SELECT COUNT(*) FROM customer WHERE deleted_at IS NULL") == 12
    assert _count(pf, "SELECT COUNT(*) FROM sales_order") == 51
    assert _count(pf, "SELECT COUNT(*) FROM supplier") == 4
    assert _count(pf, "SELECT COUNT(*) FROM warehouse") == 4


def test_reimport_is_idempotent(pf, who):
    facts = _count(pf, "SELECT COUNT(*) FROM fact")
    for mode in ("full", "incremental"):
        stats = pf.run_ingestion(who("ingest.bot"), mode)["stats"]
        assert stats.get("facts_added", 0) == 0 and stats.get("created", 0) == 0 and stats.get("updated", 0) == 0
        assert stats["inferred_added"] == 0 and stats["inferred_retracted"] == 0
    assert _count(pf, "SELECT COUNT(*) FROM fact") == facts


def test_canonical_ids_are_deterministic_across_rebuilds(tmp_path, pf):
    root = tmp_path / "other"
    generate(root / "sources")
    other = Platform(make_settings(root))
    other.run_ingestion(other.auth.principal("ingest.bot"), "full")
    a = dict(pf.conn.execute("SELECT source_system||'/'||source_id, canonical_id FROM id_map").fetchall())
    b = dict(other.conn.execute("SELECT source_system||'/'||source_id, canonical_id FROM id_map").fetchall())
    assert a == b


def test_crm_enrichment_matched_by_normalized_email_and_deduplicated(pf):
    c2 = canonical(pf, "C002")
    row = pf.conn.execute("SELECT segment, phone FROM customer WHERE customer_id=?", (c2,)).fetchone()
    # CRM-0102 (plus récent, email en casse différente) l'emporte sur CRM-0002.
    assert row["segment"] == "GRAND_COMPTE" and row["phone"] == "+33 1 99 99 99 99"
    maps = pf.conn.execute("SELECT source_id, match_rule FROM id_map WHERE canonical_id=? ORDER BY source_id", (c2,)).fetchall()
    assert [tuple(m) for m in maps] == [("C002", "ANCHOR"), ("CRM-0002", "ID-EMAIL-DUPLICATE"), ("CRM-0102", "ID-EMAIL")]
    seg = current_facts(pf, c2, "customer.segment")
    assert len(seg) == 1 and seg[0]["source_id"] == "CRM-0102"


def test_prospect_not_imported_and_fuzzy_match_pending_review(pf):
    assert _count(pf, "SELECT COUNT(*) FROM id_map WHERE source_id='CRM-0099'") == 0
    kinds = {r["source_id"]: r["kind"] for r in pf.conn.execute("SELECT source_id, kind FROM ingestion_issue")}
    assert kinds["CRM-0099"] == "not_a_customer"
    assert kinds["CRM-0105"] == "identity_review"
    cand = current_facts(pf, canonical(pf, "C005"), "possibleCrmMatch")
    assert len(cand) == 1 and cand[0]["validation_status"] == "pending_review" and cand[0]["method"] == "inference"
    # Non fusionné : l'email ERP reste celui du client.
    assert pf.conn.execute("SELECT email FROM customer WHERE customer_id=?", (canonical(pf, "C005"),)).fetchone()[0] \
        == "compta@garnier-industrie.fr"


def test_invalid_line_is_quarantined(pf):
    issue = pf.conn.execute("SELECT * FROM ingestion_issue WHERE kind='validation'").fetchone()
    assert issue["source_id"].startswith("SO-10010#") and "quantity" in issue["detail"]
    assert _count(pf, "SELECT COUNT(*) FROM id_map WHERE source_id=?", issue["source_id"]) == 0


def test_source_change_propagates_to_all_layers(pf, who, erp):
    cust = canonical(pf, "C004")
    idf_before = pf.query_metric(who("farid.analyst"), "revenue", filters={"customer.region": "IDF"})["value"]
    ara_customer_revenue = pf.query_metric(who("farid.analyst"), "revenue", filters={"customer.customer_id": cust})["value"]
    erp.execute("UPDATE ERP_CUSTOMER SET REGION='IDF', UPDATED_AT='2026-07-01T08:00:00Z' WHERE CUST_NO='C004'")
    erp.commit()
    stats = pf.run_ingestion(who("ingest.bot"), "incremental")["stats"]
    assert stats["cdc_events"] == 1 and stats["updated"] == 1
    # Core
    assert pf.conn.execute("SELECT region FROM customer WHERE customer_id=?", (cust,)).fetchone()[0] == "IDF"
    # Knowledge graph : ancien fait historisé, nouveau fait courant avec provenance.
    hist = pf.conn.execute("SELECT object_value, valid_from, valid_to, superseded_at FROM fact WHERE subject=? "
                           "AND predicate='customer.region' ORDER BY fact_id", (cust,)).fetchall()
    assert [json.loads(h["object_value"]) for h in hist] == ["ARA", "IDF"]
    assert hist[0]["superseded_at"] and hist[0]["valid_to"] == "2026-07-01T08:00:00Z"
    assert hist[1]["superseded_at"] is None and hist[1]["valid_from"] == "2026-07-01T08:00:00Z"
    # Modèle sémantique
    idf_after = pf.query_metric(who("farid.analyst"), "revenue", filters={"customer.region": "IDF"})["value"]
    assert abs(idf_after - (idf_before + ara_customer_revenue)) < 0.01
    # Périmètre : la commerciale IDF voit désormais ce client.
    assert pf.get_entity(who("chloe.rep"), cust)["canonical_id"] == cust
    assert pf.reconcile(who("alice.admin"))["consistent"]


def test_deletion_in_source_retracts_facts_and_inferences(pf, who, erp):
    order = canonical(pf, "SO-10001")
    rev_before = pf.query_metric(who("farid.analyst"), "revenue")["value"]
    order_rev = pf.query_metric(who("farid.analyst"), "revenue", filters={"time.day": "2026-02-01"})["value"]
    erp.execute("DELETE FROM ERP_ORDER_LINE WHERE ORD_NO='SO-10001'")
    erp.execute("DELETE FROM ERP_ORDER WHERE ORD_NO='SO-10001'")
    erp.commit()
    stats = pf.run_ingestion(who("ingest.bot"), "incremental")["stats"]
    assert stats["deleted"] == 3
    assert pf.conn.execute("SELECT deleted_at FROM entity WHERE canonical_id=?", (order,)).fetchone()[0]
    assert pf.conn.execute("SELECT deleted_at FROM sales_order WHERE order_id=?", (order,)).fetchone()[0]
    assert current_facts(pf, order, "order.status") == []
    assert current_facts(pf, order, "dependsOnSupplier") == []          # inférence rétractée
    assert pf.conn.execute("SELECT COUNT(*) FROM fact WHERE subject=?", (order,)).fetchone()[0] > 0  # historique conservé
    rev_after = pf.query_metric(who("farid.analyst"), "revenue")["value"]
    assert abs(rev_before - order_rev - rev_after) < 0.01
    assert pf.reconcile(who("alice.admin"))["consistent"]


def test_file_source_change_and_deletion(pf, who, env):
    path = env / "sources" / "crm_contacts.csv"
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    for r in rows:
        if r["contact_id"] == "CRM-0001":
            r["segment"] = "GRAND_COMPTE"
    rows = [r for r in rows if r["contact_id"] != "CRM-0003"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    stats = pf.run_ingestion(who("ingest.bot"), "incremental")["stats"]
    assert stats["updated"] == 1 and stats["deleted"] == 1
    assert pf.conn.execute("SELECT segment FROM customer WHERE customer_id=?", (canonical(pf, "C001"),)).fetchone()[0] == "GRAND_COMPTE"
    c3 = canonical(pf, "C003")
    # Suppression d'un enrichissement : le client reste, ses attributs CRM disparaissent.
    row = pf.conn.execute("SELECT deleted_at, segment FROM customer WHERE customer_id=?", (c3,)).fetchone()
    assert row["deleted_at"] is None and row["segment"] is None
    assert current_facts(pf, c3, "customer.segment") == []
    assert pf.reconcile(who("alice.admin"))["consistent"]
