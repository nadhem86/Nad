"""Sécurité et gouvernance : permissions côté serveur, périmètres, champs sensibles,
canal agent plafonné, authentification, journal d'audit infalsifiable."""

import pytest
from conftest import canonical

from okp.operations.actions import ActionError
from okp.security.policy import Forbidden, NotFound, Unauthenticated


def test_role_without_permission_cannot_execute(pf, who):
    with pytest.raises(ActionError) as exc:
        pf.execute_action(who("farid.analyst"), "confirm_order", {"order_id": "SO-20001", "idempotency_key": "k-sec-0001"})
    assert exc.value.code == "FORBIDDEN"
    with pytest.raises(ActionError) as exc:
        pf.execute_action(who("emma.ops"), "confirm_order", {"order_id": "SO-20001", "idempotency_key": "k-sec-0002"})
    assert exc.value.code == "FORBIDDEN"


def test_region_scope_on_entities_actions_and_metrics(pf, who):
    chloe = who("chloe.rep")  # IDF
    with pytest.raises(NotFound):
        pf.get_entity(chloe, canonical(pf, "SO-20002"))  # client ARA
    with pytest.raises(ActionError) as exc:
        pf.execute_action(chloe, "confirm_order", {"order_id": "SO-20002", "idempotency_key": "k-scope-001"})
    assert exc.value.code == "NOT_FOUND"
    assert pf.resolve_entity(chloe, "SO-20002")["status"] == "not_found"
    mine = pf.query_metric(chloe, "revenue")
    idf = pf.query_metric(who("farid.analyst"), "revenue", filters={"customer.region": "IDF"})
    assert mine["value"] == idf["value"] and mine["security_filters"] == {"customer.region": ["IDF"]}
    # Le filtre utilisateur ne peut pas élargir le périmètre (intersection).
    other = pf.query_metric(chloe, "revenue", filters={"customer.region": ["ARA"]})
    assert other["status"] == "no_data"
    # David (ARA, OCC) peut confirmer SO-20002.
    r = pf.execute_action(who("david.rep"), "confirm_order", {"order_id": "SO-20002", "idempotency_key": "k-scope-002"})
    assert r["new_status"] == "confirmed"


def test_warehouse_scope(pf, who):
    with pytest.raises(ActionError) as exc:
        pf.execute_action(who("emma.ops"), "assign_warehouse",
                          {"order_id": "SO-20004", "warehouse_id": "PAR1", "idempotency_key": "k-wh-00001"})
    assert exc.value.code == "FORBIDDEN"


def test_metric_permissions_and_sensitivity(pf, who):
    with pytest.raises(Forbidden):
        pf.query_metric(who("chloe.rep"), "gross_margin")
    with pytest.raises(Forbidden):
        pf.query_metric(who("emma.ops"), "revenue")
    assert pf.query_metric(who("bruno.manager"), "gross_margin")["status"] == "ok"


def test_scope_that_cannot_apply_denies_instead_of_leaking(pf):
    from okp.semantic.engine import MetricError
    with pytest.raises(MetricError) as exc:
        pf.metrics.query("stock_on_hand", security_filters={"customer.region": ["IDF"]})
    assert exc.value.code == "FORBIDDEN_SCOPE"


def test_field_level_masking(pf, who):
    cid = canonical(pf, "C001")
    analyst = pf.get_entity(who("farid.analyst"), cid)
    assert "email" in analyst["masked_properties"] and "email" not in analyst["properties"]
    manager = pf.get_entity(who("bruno.manager"), cid)
    assert manager["properties"]["email"][0]["value"] == "contact@boulangerie-martin.fr"
    line = canonical(pf, "SO-10001#1")
    assert "unit_cost_eur" in pf.get_entity(who("chloe.rep"), line)["masked_properties"]
    assert "unit_cost_eur" in pf.get_entity(who("farid.analyst"), line)["properties"]


def test_entity_type_permission(pf, who):
    with pytest.raises(NotFound):
        pf.get_entity(who("emma.ops"), canonical(pf, "C001"))


def test_agent_channel_is_capped_even_for_admin(pf, who):
    admin_agent = who("alice.admin", "agent")
    entity = pf.get_entity(admin_agent, canonical(pf, "C001"))
    assert "email" in entity["masked_properties"]
    with pytest.raises(ActionError) as exc:
        pf.execute_action(admin_agent, "cancel_order", {"order_id": "SO-20001", "reason": "test agent", "idempotency_key": "k-agent-001"})
    assert exc.value.code == "FORBIDDEN"
    with pytest.raises(Forbidden):
        pf.audit_events(admin_agent)
    # Le même principal sur le canal API peut annuler.
    assert pf.execute_action(who("alice.admin"), "cancel_order",
                             {"order_id": "SO-20001", "reason": "test humain", "idempotency_key": "k-human-001"})["new_status"] == "cancelled"


def test_authentication(pf):
    with pytest.raises(Unauthenticated):
        pf.authenticate("wrong-key", "api")
    with pytest.raises(Unauthenticated):
        pf.authenticate(None, "api")
    p = pf.authenticate("demo-rep-idf-key", "agent")
    assert p.id == "chloe.rep" and p.channel == "agent"
    assert pf.audit.events(1)[0]["event_type"] == "auth.failure"


def test_audit_log_records_decisions_and_detects_tampering(pf, who):
    with pytest.raises(ActionError):
        pf.execute_action(who("farid.analyst"), "confirm_order", {"order_id": "SO-20001", "idempotency_key": "k-audit-01"})
    pf.execute_action(who("chloe.rep"), "confirm_order", {"order_id": "SO-20001", "idempotency_key": "k-audit-02"})
    events = pf.audit.events(10)
    decisions = {(e["principal_id"], e["event_type"], e["decision"]) for e in events}
    assert ("farid.analyst", "action.confirm_order", "deny") in decisions
    assert ("chloe.rep", "action.confirm_order", "success") in decisions
    assert all("model_versions" not in e for e in events)
    assert pf.audit.verify()["valid"]
    # Append-only : UPDATE/DELETE refusés par triggers.
    with pytest.raises(Exception):
        pf.conn.execute("UPDATE audit_log SET decision='allow' WHERE seq=1")
    # Altération en contournant les triggers → détectée par le chaînage HMAC.
    pf.conn.execute("DROP TRIGGER audit_no_update")
    pf.conn.execute("UPDATE audit_log SET decision='success' WHERE event_type='action.confirm_order' AND decision='deny'")
    report = pf.audit.verify()
    assert not report["valid"] and report["first_invalid_seq"] is not None


def test_audit_details_do_not_contain_pii(pf, who):
    pf.get_entity(who("bruno.manager"), canonical(pf, "C001"))
    details = " ".join(r[0] for r in pf.conn.execute("SELECT details FROM audit_log"))
    assert "@" not in details


def test_secrets_not_stored_in_clear():
    from okp.config import PROJECT_ROOT
    text = (PROJECT_ROOT / "config" / "principals.demo.yaml").read_text()
    assert "demo-admin-key" not in text and "api_key_sha256" in text


def test_prod_requires_audit_secret(monkeypatch):
    from okp.config import Settings
    monkeypatch.delenv("OKP_AUDIT_HMAC_KEY", raising=False)
    with pytest.raises(RuntimeError):
        Settings.from_env(env="prod")
