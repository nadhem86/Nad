"""Import initial, idempotence, déduplication, résolution d'identité, incrémental, suppressions."""

from hub.ingest.pipeline import ingest
from tests.conftest import iri_of

SNAP = "vtiger/snapshot_2026-09-30.json"
DELTA = "vtiger/delta_001_2026-09-30.json"


def _count(hub, sql="SELECT COUNT(*) FROM fact"):
    return hub.conn.execute(sql).fetchone()[0]


def test_initial_import_counts(hub):
    assert len(hub.kg.entities("Commande")) == 10
    assert len(hub.kg.entities("LigneCommande")) == 17
    assert len(hub.kg.entities("Client")) == 5
    assert len(hub.kg.entities("IdentiteWhatsApp")) == 5  # 6 enregistrements, 1 doublon fusionné


def test_reimport_is_idempotent(hub):
    before = _count(hub)
    for src, path in (("vtiger", SNAP), ("wms", "wms"), ("whatsapp", "whatsapp/whatsapp-export.json")):
        r = ingest(hub, src, hub.settings.demo_dir / path)
        assert r["stats"].get("facts_inserted", 0) == 0 and r["stats"].get("facts_superseded", 0) == 0, r["stats"]
        assert all(v.get("inserted", 0) == 0 for v in r["inference"].values())
    assert _count(hub) == before


def test_iris_are_deterministic(tmp_path, monkeypatch, hub):
    from hub.ingest.pipeline import load_demo
    from hub.services import Hub

    monkeypatch.setenv("NAD_DB_PATH", str(tmp_path / "other.sqlite"))
    other = Hub()
    load_demo(other)
    assert set(hub.kg.entities("Commande")) == set(other.kg.entities("Commande"))


def test_whatsapp_dedup_and_minimisation(hub):
    waid = iri_of(hub, "+33612345678", "IdentiteWhatsApp")
    ids = hub.kg.source_ids(waid)
    assert [i["source_id"] for i in ids] == ["+33612345678"]
    assert hub.kg.get(waid, "nomAffiche") == "Jeanne Martin 🥖"  # priorité Store.Contact sur DOM-chatlist
    stored = " ".join(r[0] or "" for r in hub.conn.execute("SELECT object_value FROM fact"))
    assert "livrer jeudi" not in stored and "Boulangère depuis" not in stored  # messages / statut non ingérés


def test_cross_source_identity_resolution(hub):
    so102 = iri_of(hub, "SO102")
    systems = {(i["source"], i["source_type"], i["source_id"]) for i in hub.kg.source_ids(so102)}
    assert ("vtiger", "SalesOrder", "6x102") in systems and ("wms", "salesorder_no", "SO102") in systems


def test_unresolved_reference_is_rejected_not_invented(hub, tmp_path):
    import shutil
    d = tmp_path / "wms"
    shutil.copytree(hub.settings.demo_dir / "wms", d)
    with open(d / "events.csv", "a") as f:
        f.write("E99,SO999,affectation,2026-09-30T10:00:00Z,LIL,,,,\n")
    r = ingest(hub, "wms", d)
    assert any(x.get("salesorder_no") == "SO999" for x in r["rejected"])
    assert hub.kg.find_by_value("Commande", "numeroCommande", "SO999") == []


def test_incremental_update_supersedes_and_keeps_history(hub):
    line = hub.kg.resolve_source("vtiger", "LineItem", "1003")
    old = hub.kg.fact_for(line, "quantite")
    r = ingest(hub, "vtiger", hub.settings.demo_dir / DELTA)
    assert r["mode"] == "delta"
    assert hub.kg.get(line, "quantite") == 6.0
    hist = hub.conn.execute("SELECT superseded_at, valid_to FROM fact WHERE fact_id=?", (old["fact_id"],)).fetchone()
    assert hist["superseded_at"] and hist["valid_to"] == "2026-09-30T10:00:00Z"


def test_historical_cost_used_for_past_orders(hub):
    ingest(hub, "vtiger", hub.settings.demo_dir / DELTA)
    pro1 = iri_of(hub, "PRO1")
    assert hub.kg.get(pro1, "coutUnitaire") == 22.0
    assert hub.kg.value_at(pro1, "coutUnitaire", "2026-07-03T08:15:00Z") == (21.0, True)
    row = hub.conn.execute("SELECT cout_unitaire_eur FROM fct_ligne l JOIN fct_commande c USING(commande_iri) "
                           "WHERE c.numero='SO101' AND l.produit_iri=?", (pro1,)).fetchone()
    assert row[0] == 21.0


def test_deletion_tombstones_entity_and_retracts_facts(hub):
    con4 = hub.kg.resolve_source("vtiger", "Contacts", "12x4")
    ingest(hub, "vtiger", hub.settings.demo_dir / DELTA)
    assert hub.kg.entity(con4)["deleted_at"]
    assert hub.kg.facts(con4, include_inferred=False) == []
    assert con4 not in hub.kg.entities("Contact")


def test_snapshot_absence_means_deletion(hub, tmp_path):
    import json
    snap = json.loads((hub.settings.demo_dir / SNAP).read_text())
    snap["SalesOrder"] = [o for o in snap["SalesOrder"] if o["salesorder_no"] != "SO108"]
    p = tmp_path / "snap.json"
    p.write_text(json.dumps(snap))
    so108 = iri_of(hub, "SO108")
    lines = [l for l, _ in hub.kg.traverse(so108, ["^ligneDe"])]
    r = ingest(hub, "vtiger", p)
    assert r["stats"]["retracted_SalesOrder"] == 1
    assert hub.kg.entity(so108)["deleted_at"] and all(hub.kg.entity(l)["deleted_at"] for l in lines)
    assert hub.conn.execute("SELECT COUNT(*) FROM fct_commande WHERE numero='SO108'").fetchone()[0] == 0


def test_invalid_source_record_rejected_without_aborting(hub, tmp_path):
    import json
    snap = json.loads((hub.settings.demo_dir / SNAP).read_text())
    snap["SalesOrder"][0]["sostatus"] = "Bizarre"
    p = tmp_path / "snap.json"
    p.write_text(json.dumps(snap))
    r = ingest(hub, "vtiger", p)
    assert r["rejected"] and "sostatus inconnu" in r["rejected"][0]["reason"]
