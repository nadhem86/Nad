"""Transitions, idempotence des actions, autorité de la source, cohérence des représentations dérivées."""

import pytest

from hub.ingest.pipeline import ingest
from hub.operations import ActionError
from tests.conftest import iri_of


def P(hub, pid):
    return hub.policy.principal(pid)


def test_confirm_updates_source_kg_inference_and_projection(hub):
    so106 = iri_of(hub, "SO106")
    r = hub.orders.execute(P(hub, "bruno.commercial.nord"), "confirmer", {"commande": so106}, "k1")
    assert (r["statut_avant"], r["statut_apres"]) == ("brouillon", "confirmee")
    w = hub.conn.execute("SELECT * FROM sim_source_write").fetchone()
    assert w["system"] == "vtiger" and w["record_id"] == "6x106" and '"Approved"' in w["patch"]
    f = hub.kg.fact_for(so106, "statutCRM")
    assert f["method"] == "saisie" and f["actor"] == "bruno.commercial.nord" and f["source"] == "vtiger"
    assert hub.conn.execute("SELECT statut FROM fct_commande WHERE commande_iri=?", (so106,)).fetchone()[0] == "confirmee"
    from hub.projection import is_stale
    assert not is_stale(hub.conn)


def test_source_remains_authority_after_reingest(hub):
    """Après une action, relire la source (qui contient désormais l'écriture) ne change rien : pas de conflit."""
    so106 = iri_of(hub, "SO106")
    hub.orders.execute(P(hub, "bruno.commercial.nord"), "confirmer", {"commande": so106}, "k1")
    r = ingest(hub, "vtiger", hub.settings.demo_dir / "vtiger" / "snapshot_2026-09-30.json")
    assert r["stats"]["patches_simules_appliques"] == 1
    assert r["stats"].get("facts_superseded", 0) == 0
    assert hub.kg.get(so106, "statut") == "confirmee"


@pytest.mark.parametrize("so,action,params", [
    ("SO101", "confirmer", {}),                       # livrée -> confirmer
    ("SO102", "annuler", {"motif": "trop tard"}),    # expédiée -> annuler
    ("SO106", "expedier", {"transporteur": "X", "numero_suivi": "1"}),  # brouillon -> expédier
    ("SO105", "confirmer", {}),                       # annulée (terminal)
])
def test_invalid_transitions_rejected(hub, so, action, params):
    iri = iri_of(hub, so)
    before = hub.kg.get(iri, "statut")
    with pytest.raises(ActionError) as e:
        hub.orders.execute(P(hub, "alice.admin"), action, {"commande": iri, **params}, f"k-{so}-{action}")
    assert e.value.code == "INVALID_TRANSITION"
    assert hub.kg.get(iri, "statut") == before
    assert hub.conn.execute("SELECT COUNT(*) FROM sim_source_write").fetchone()[0] == 0


def test_full_lifecycle_confirm_assign_ship(hub):
    so106 = iri_of(hub, "SO106")
    lil = iri_of(hub, "LIL")
    admin = P(hub, "alice.admin")
    hub.orders.execute(admin, "confirmer", {"commande": so106}, "a1")
    hub.orders.execute(P(hub, "david.logisticien.lille"), "affecter_entrepot", {"commande": so106, "entrepot": lil}, "a2")
    assert hub.kg.get(so106, "statut") == "en_preparation"
    hub.orders.execute(P(hub, "david.logisticien.lille"), "expedier",
                       {"commande": so106, "transporteur": "Geodis", "numero_suivi": "GD106"}, "a3")
    assert hub.kg.get(so106, "statut") == "expediee"
    # la relecture des sources (vTiger + WMS simulés) reproduit le même état
    ingest(hub, "vtiger", hub.settings.demo_dir / "vtiger" / "snapshot_2026-09-30.json")
    r = ingest(hub, "wms", hub.settings.demo_dir / "wms")
    assert r["stats"]["evenements_simules"] == 2 and hub.kg.get(so106, "statut") == "expediee"


def test_stock_validation(hub):
    so107 = iri_of(hub, "SO107")  # 3 x PRO2 : MRS n'en a qu'1
    with pytest.raises(ActionError) as e:
        hub.orders.execute(P(hub, "alice.admin"), "affecter_entrepot", {"commande": so107, "entrepot": iri_of(hub, "MRS")}, "s1")
    assert e.value.code == "STOCK_INSUFFISANT" and e.value.details[0]["produit"] == "PRO2"


def test_cancel_requires_reason_and_extra_permission_in_preparation(hub):
    so107 = iri_of(hub, "SO107")
    with pytest.raises(ActionError) as e:
        hub.orders.execute(P(hub, "bruno.commercial.nord"), "annuler", {"commande": so107, "motif": ""}, "c1")
    assert e.value.code == "VALIDATION_ERROR"
    so103 = iri_of(hub, "SO103")  # en_preparation, Sud
    with pytest.raises(ActionError) as e:
        hub.orders.execute(P(hub, "chloe.commerciale.sud"), "annuler", {"commande": so103, "motif": "client parti"}, "c2")
    assert e.value.code == "FORBIDDEN" and "annuler_en_preparation" in str(e.value)


def test_action_idempotency_replay_and_conflict(hub):
    so106 = iri_of(hub, "SO106")
    bruno = P(hub, "bruno.commercial.nord")
    r1 = hub.orders.execute(bruno, "confirmer", {"commande": so106}, "same-key")
    n_facts = hub.conn.execute("SELECT COUNT(*) FROM fact").fetchone()[0]
    r2 = hub.orders.execute(bruno, "confirmer", {"commande": so106}, "same-key")
    assert r2["replayed"] and r2["statut_apres"] == r1["statut_apres"]
    assert hub.conn.execute("SELECT COUNT(*) FROM fact").fetchone()[0] == n_facts
    assert hub.conn.execute("SELECT COUNT(*) FROM sim_source_write").fetchone()[0] == 1
    with pytest.raises(ActionError) as e:
        hub.orders.execute(bruno, "annuler", {"commande": so106, "motif": "autre requête"}, "same-key")
    assert e.value.code == "IDEMPOTENCY_CONFLICT"
    with pytest.raises(ActionError) as e:
        hub.orders.execute(bruno, "confirmer", {"commande": so106}, "")
    assert e.value.code == "VALIDATION_ERROR"


def test_source_failure_leaves_kg_untouched(hub):
    so106 = iri_of(hub, "SO106")
    hub.gateways["vtiger"].fail_next = True
    n = hub.conn.execute("SELECT COUNT(*) FROM fact").fetchone()[0]
    with pytest.raises(ActionError) as e:
        hub.orders.execute(P(hub, "bruno.commercial.nord"), "confirmer", {"commande": so106}, "f1")
    assert e.value.code == "SOURCE_UNAVAILABLE"
    assert hub.conn.execute("SELECT COUNT(*) FROM fact").fetchone()[0] == n
    assert hub.kg.get(so106, "statut") == "brouillon"
    # la même clé peut être rejouée une fois la source rétablie
    assert hub.orders.execute(P(hub, "bruno.commercial.nord"), "confirmer", {"commande": so106}, "f1")["statut_apres"] == "confirmee"


def test_kg_failure_after_source_write_is_logged_as_desync(hub, monkeypatch):
    so106 = iri_of(hub, "SO106")
    import hub.operations as ops

    monkeypatch.setattr(ops.inference, "run_all", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("panne")))
    with pytest.raises(ActionError) as e:
        hub.orders.execute(P(hub, "bruno.commercial.nord"), "confirmer", {"commande": so106}, "d1")
    assert e.value.code == "SYNC_PENDING"
    assert hub.conn.execute("SELECT COUNT(*) FROM audit_log WHERE kind='desync'").fetchone()[0] == 1
    assert hub.kg.get(so106, "statutCRM") == "brouillon"  # transaction annulée
    monkeypatch.undo()
    ingest(hub, "vtiger", hub.settings.demo_dir / "vtiger" / "snapshot_2026-09-30.json")  # réconciliation par la source
    assert hub.kg.get(so106, "statut") == "confirmee"
