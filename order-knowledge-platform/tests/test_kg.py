"""Knowledge graph : validité (SHACL), provenance, inférences traçables, requêtes de relations."""

import json

from conftest import canonical
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, XSD

from okp.ontology.validate import export_dataset, validate_graph, validate_kg


def test_demo_graph_conforms_to_shacl(pf):
    report = validate_kg(pf.conn, pf.registry.glossary)
    assert report["conforms"], report["violations"][:5]
    assert report["triples"] > 1000


def test_shacl_detects_invalid_entities_and_relations(pf):
    g = pf.registry.glossary
    OKP, INST = Namespace(g.namespace), Namespace(g.instance_namespace)
    data = Graph()
    o, line, prod = INST["ORD-BAD"], INST["OLN-BAD"], INST["PRD-X"]
    data.add((o, RDF.type, OKP.Order))
    for p, v, t in (("status", "shipped", XSD.string), ("erp_order_no", "X", XSD.string), ("currency", "EUR", XSD.string),
                    ("ordered_at", "2026-01-01T00:00:00Z", XSD.dateTime), ("order_date_local", "2026-01-01", XSD.date),
                    ("confirmed_at", "2026-01-02T00:00:00Z", XSD.dateTime), ("shipped_at", "2026-01-01T00:00:00Z", XSD.dateTime)):
        data.add((o, OKP[f"order.{p}"], Literal(v, datatype=t)))
    data.add((o, OKP.hasLine, line))
    data.add((line, RDF.type, OKP.OrderLine))
    data.add((line, OKP["orderline.quantity"], Literal(1)))
    data.add((line, OKP["orderline.returned_quantity"], Literal(3)))
    data.add((prod, RDF.type, OKP.Product))
    report = validate_graph(data, g)
    assert not report["conforms"]
    messages = " ".join(v["message"] for v in report["violations"])
    for expected in ("C-ORD-WAREHOUSE", "C-ORD-SHIP-DATES", "C-LINE-RETURNS"):
        assert expected in messages
    paths = {v["path"] for v in report["violations"]}
    assert str(OKP.placedBy) in paths          # commande sans client (minCount 1)
    assert str(OKP.refersToProduct) in paths   # ligne sans produit


def test_every_fact_carries_provenance(pf):
    bad = pf.conn.execute(
        "SELECT COUNT(*) FROM fact WHERE source_system IS NULL OR source_id IS NULL OR collected_at IS NULL "
        "OR method NOT IN ('import','manual','inference') OR validation_status IS NULL").fetchone()[0]
    assert bad == 0
    imported = pf.conn.execute("SELECT COUNT(*) FROM fact WHERE method='import' AND source_system NOT IN ('erp','crm','wms','srm')").fetchone()[0]
    assert imported == 0


def test_inferred_facts_are_separated_and_traceable(pf):
    rows = pf.conn.execute("SELECT * FROM fact WHERE method='inference' AND source_system='okp'").fetchall()
    assert rows
    declared = {r.rule_id for r in pf.registry.glossary.inferred_relations.values()}
    for f in rows:
        assert f["rule_id"] in declared
        evidence = json.loads(f["derived_from"])
        assert evidence
        found = pf.conn.execute(f"SELECT COUNT(*) FROM fact WHERE fact_id IN ({','.join('?' * len(evidence))}) "
                                "AND method='import'", evidence).fetchone()[0]
        assert found == len(evidence), "chaque preuve d'une inférence est un fait source existant"
    ds = export_dataset(pf.conn, pf.registry.glossary)
    inferred_graph = ds.graph(URIRef(f"{pf.registry.glossary.instance_namespace}graph/inferred"))
    erp_graph = ds.graph(URIRef(f"{pf.registry.glossary.instance_namespace}graph/erp"))
    OKP = Namespace(pf.registry.glossary.namespace)
    assert len(list(inferred_graph.triples((None, OKP.dependsOnSupplier, None)))) > 0
    assert len(list(erp_graph.triples((None, OKP.dependsOnSupplier, None)))) == 0


def test_inference_follows_business_rule(pf):
    # R-PURCHASE exclut brouillons et annulées : C001 n'a qu'un brouillon (SO-20001) s'il n'a pas d'autre commande.
    c001 = canonical(pf, "C001")
    purchased = {r["object_id"] for r in pf.conn.execute(
        "SELECT object_id FROM fact WHERE subject=? AND predicate='purchased' AND retracted_at IS NULL", (c001,))}
    expected = {r[0] for r in pf.conn.execute(
        "SELECT DISTINCT l.product_id FROM sales_order o JOIN order_line l ON l.order_id=o.order_id "
        "WHERE o.customer_id=? AND o.status NOT IN ('draft','cancelled') AND l.deleted_at IS NULL", (c001,))}
    assert purchased == expected


def test_order_dependencies_and_supplier_impact(pf, who):
    ov = pf.order_overview(who("bruno.manager"), "SO-10001")
    preds = {(r["predicate"], r["object"]["concept"]) for r in ov["relations"]["observed"]}
    assert {("placedBy", "Customer"), ("hasLine", "OrderLine"), ("refersToProduct", "Product"),
            ("suppliedBy", "Supplier"), ("fulfilledFrom", "Warehouse")} <= preds
    assert all(r["provenance"]["kind"] == "inferred" for r in ov["relations"]["inferred"])
    impact = pf.supplier_impact(who("bruno.manager"), "SRM-01")
    assert impact["status"] == "ok" and impact["impacted_orders"]
    statuses = {o["status"] for o in impact["impacted_orders"]}
    assert statuses <= {"draft", "confirmed", "in_preparation"}
    # Cohérent avec un calcul direct sur la couche relationnelle.
    direct = {r[0] for r in pf.conn.execute(
        "SELECT DISTINCT o.erp_order_no FROM sales_order o JOIN order_line l ON l.order_id=o.order_id "
        "JOIN product_supplier ps ON ps.product_id=l.product_id JOIN supplier s ON s.supplier_id=ps.supplier_id "
        "JOIN id_map m ON m.canonical_id=s.supplier_id AND m.source_id='SRM-01' "
        "WHERE o.status IN ('draft','confirmed','in_preparation') AND o.deleted_at IS NULL AND l.deleted_at IS NULL")}
    assert {o["erp_order_no"] for o in impact["impacted_orders"]} == direct


def test_identifier_consistency_between_layers(pf, who):
    assert pf.reconcile(who("alice.admin")) == {"consistent": True, "issues": [], "issue_count": 0}
    # Les identifiants du modèle sémantique sont les identifiants canoniques du graphe.
    ids = {r[0] for r in pf.conn.execute("SELECT DISTINCT customer_id FROM v_fact_order")}
    kg_ids = {r[0] for r in pf.conn.execute("SELECT canonical_id FROM entity WHERE concept='Customer'")}
    assert ids <= kg_ids


def test_status_history_is_kept(pf, who):
    oid = canonical(pf, "SO-20001")
    pf.execute_action(who("chloe.rep"), "confirm_order", {"order_id": "SO-20001", "idempotency_key": "hist-key-001"})
    history = pf.conn.execute("SELECT object_value, superseded_at FROM fact WHERE subject=? AND predicate='order.status' "
                              "ORDER BY fact_id", (oid,)).fetchall()
    assert [json.loads(h[0]) for h in history] == ["draft", "confirmed"]
    assert history[0][1] is not None and history[1][1] is None
