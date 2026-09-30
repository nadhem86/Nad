"""Couche opérationnelle : transitions, préconditions, autorité de la source, idempotence,
propagation vers les représentations dérivées, erreurs."""

import pytest
from conftest import canonical, current_facts

from okp.operations.actions import ActionError


def _erp_status(erp, order_no):
    return erp.execute("SELECT STATUS_CD, ROW_VERSION FROM ERP_ORDER WHERE ORD_NO=?", (order_no,)).fetchone()


def test_confirm_updates_source_then_derived_layers(pf, who, erp):
    c001 = canonical(pf, "C001")
    purchased_before = {f["object_id"] for f in current_facts(pf, c001, "purchased")}
    r = pf.execute_action(who("chloe.rep"), "confirm_order", {"order_id": "SO-20001", "idempotency_key": "k-confirm-001"})
    assert (r["previous_status"], r["new_status"], r["sync_status"]) == ("draft", "confirmed", "synced")
    assert r["authority"]["system"] == "erp"
    assert _erp_status(erp, "SO-20001")["STATUS_CD"] == "CNF"                        # source faisant autorité
    oid = canonical(pf, "SO-20001")
    assert pf.conn.execute("SELECT status FROM sales_order WHERE order_id=?", (oid,)).fetchone()[0] == "confirmed"
    status_fact = current_facts(pf, oid, "order.status")[0]
    assert status_fact["source_system"] == "erp" and status_fact["method"] == "import"
    purchased_after = {f["object_id"] for f in current_facts(pf, c001, "purchased")}  # inférence R-PURCHASE
    assert purchased_after == purchased_before | {canonical(pf, "P104"), canonical(pf, "P105")}
    assert pf.reconcile(who("alice.admin"))["consistent"]


def test_action_is_idempotent(pf, who, erp):
    args = {"order_id": "SO-20001", "idempotency_key": "k-idem-0001"}
    first = pf.execute_action(who("chloe.rep"), "confirm_order", args)
    version = _erp_status(erp, "SO-20001")["ROW_VERSION"]
    second = pf.execute_action(who("chloe.rep"), "confirm_order", args)
    assert second["replayed"] is True and second["new_status"] == first["new_status"]
    assert _erp_status(erp, "SO-20001")["ROW_VERSION"] == version                    # aucun nouvel effet
    assert pf.conn.execute("SELECT COUNT(*) FROM action_log WHERE idempotency_key='k-idem-0001'").fetchone()[0] == 1


def test_idempotency_key_reuse_with_other_request_conflicts(pf, who):
    pf.execute_action(who("chloe.rep"), "confirm_order", {"order_id": "SO-20001", "idempotency_key": "k-reuse-001"})
    with pytest.raises(ActionError) as exc:
        pf.execute_action(who("chloe.rep"), "cancel_order",
                          {"order_id": "SO-20001", "reason": "erreur de saisie", "idempotency_key": "k-reuse-001"})
    assert exc.value.code == "IDEMPOTENCY_CONFLICT"
    with pytest.raises(ActionError) as exc:
        pf.execute_action(who("bruno.manager"), "confirm_order", {"order_id": "SO-20001", "idempotency_key": "k-reuse-001"})
    assert exc.value.code == "IDEMPOTENCY_CONFLICT"


@pytest.mark.parametrize("action,order,params", [
    ("ship_order", "SO-20001", {"tracking_number": "TRK12345678"}),          # brouillon → expédiée
    ("cancel_order", "SO-20007", {"reason": "client absent"}),               # expédiée → annulée
    ("confirm_order", "SO-20003", {}),                                       # déjà confirmée
    ("deliver_order", "SO-20004", {}),                                       # confirmée → livrée
])
def test_invalid_transitions_rejected_without_side_effect(pf, who, erp, action, order, params):
    before = _erp_status(erp, order)
    with pytest.raises(ActionError) as exc:
        pf.execute_action(who("alice.admin"), action, {"order_id": order, "idempotency_key": f"k-inv-{action}-{order}", **params})
    assert exc.value.code == "INVALID_TRANSITION"
    assert tuple(_erp_status(erp, order)) == tuple(before)


@pytest.mark.parametrize("action,order,params,precondition", [
    ("confirm_order", "SO-20005", {}, "has_lines"),
    ("confirm_order", "SO-20006", {}, "customer_active"),
    ("confirm_order", "SO-20008", {}, "products_active"),
    ("assign_warehouse", "SO-20003", {"warehouse_id": "LYS1"}, "stock_sufficient"),
    ("assign_warehouse", "SO-20004", {"warehouse_id": "MRS9"}, "warehouse_active"),
])
def test_preconditions_enforced(pf, who, erp, action, order, params, precondition):
    before = _erp_status(erp, order)
    with pytest.raises(ActionError) as exc:
        pf.execute_action(who("alice.admin"), action, {"order_id": order, "idempotency_key": f"k-pre-{order}-{precondition}", **params})
    assert exc.value.code == "PRECONDITION_FAILED"
    assert precondition in {f["precondition"] for f in exc.value.details["failed"]}
    assert tuple(_erp_status(erp, order)) == tuple(before)


def test_warehouse_flow_assign_then_ship(pf, who, erp):
    emma = who("emma.ops")
    r = pf.execute_action(emma, "assign_warehouse", {"order_id": "SO-20004", "warehouse_id": "LYS1", "idempotency_key": "k-assign-01"})
    assert r["new_status"] == "in_preparation"
    oid = canonical(pf, "SO-20004")
    wh = current_facts(pf, oid, "fulfilledFrom")
    assert len(wh) == 1 and wh[0]["object_id"] == canonical(pf, "LYS1")
    r = pf.execute_action(emma, "ship_order", {"order_id": "SO-20004", "tracking_number": "TRK00000001", "idempotency_key": "k-ship-01"})
    assert r["new_status"] == "shipped"
    row = erp.execute("SELECT STATUS_CD, TRACKING_NO, SHIPPED_TS FROM ERP_ORDER WHERE ORD_NO='SO-20004'").fetchone()
    assert row["STATUS_CD"] == "SHP" and row["TRACKING_NO"] == "TRK00000001" and row["SHIPPED_TS"]
    assert pf.validate(who("alice.admin"))["conforms"]


def test_contract_validation(pf, who):
    for action, params in (("ship_order", {"tracking_number": "bad"}), ("cancel_order", {"reason": "x"}),
                           ("confirm_order", {"unexpected": "1"}), ("confirm_order", {"idempotency_key": "short"})):
        payload = {"order_id": "SO-20001", "idempotency_key": "k-valid-123456", **params}
        with pytest.raises(ActionError) as exc:
            pf.execute_action(who("alice.admin"), action, payload)
        assert exc.value.code == "VALIDATION_ERROR"


def test_source_conflict_detected(pf, who, erp):
    erp.execute("UPDATE ERP_ORDER SET STATUS_CD='CAN' WHERE ORD_NO='SO-20001'")  # modifié dans l'ERP, non encore synchronisé
    erp.commit()
    with pytest.raises(ActionError) as exc:
        pf.execute_action(who("chloe.rep"), "confirm_order", {"order_id": "SO-20001", "idempotency_key": "k-conflict-1"})
    assert exc.value.code == "CONFLICT"


def test_source_unavailable_leaves_everything_unchanged(pf, who, erp):
    pf.gateway.fail_next = True
    facts = pf.conn.execute("SELECT COUNT(*) FROM fact").fetchone()[0]
    with pytest.raises(ActionError) as exc:
        pf.execute_action(who("chloe.rep"), "confirm_order", {"order_id": "SO-20001", "idempotency_key": "k-down-0001"})
    assert exc.value.code == "SOURCE_UNAVAILABLE"
    assert _erp_status(erp, "SO-20001")["STATUS_CD"] == "DRF"
    assert pf.conn.execute("SELECT COUNT(*) FROM fact").fetchone()[0] == facts
    # Non mémorisé : la même clé peut être rejouée une fois la source revenue.
    r = pf.execute_action(who("chloe.rep"), "confirm_order", {"order_id": "SO-20001", "idempotency_key": "k-down-0001"})
    assert r["new_status"] == "confirmed" and r["replayed"] is False


def test_failed_resync_goes_to_outbox_then_recovers(pf, who, erp):
    original = pf.actions.sync
    pf.actions.sync = lambda: (_ for _ in ()).throw(RuntimeError("panne de synchronisation simulée"))
    r = pf.execute_action(who("chloe.rep"), "confirm_order", {"order_id": "SO-20001", "idempotency_key": "k-outbox-01"})
    assert r["sync_status"] == "pending" and r["new_status"] == "confirmed"
    assert _erp_status(erp, "SO-20001")["STATUS_CD"] == "CNF"
    rec = pf.reconcile(who("alice.admin"))
    assert not rec["consistent"] and any(i["kind"] == "pending_sync" for i in rec["issues"])
    assert pf.conn.execute("SELECT status FROM sales_order WHERE erp_order_no='SO-20001'").fetchone()[0] == "draft"
    pf.actions.sync = original
    assert pf.process_outbox(who("ingest.bot"))["processed"] == 1
    assert pf.conn.execute("SELECT status FROM sales_order WHERE erp_order_no='SO-20001'").fetchone()[0] == "confirmed"
    assert pf.reconcile(who("alice.admin"))["consistent"]


def test_available_actions_are_side_effect_free(pf, who, erp):
    before = _erp_status(erp, "SO-20003")
    acts = {a["action"]: a for a in pf.available_actions(who("emma.ops"), "SO-20003")["actions"]}
    assign = acts["assign_warehouse"]
    assert assign["transition_valid"] and assign["allowed_by_policy"] and assign["to_state"] == "in_preparation"
    assert {c["precondition"]: c["satisfied"] for c in assign["preconditions"]} == \
        {"warehouse_active": None, "stock_sufficient": None}  # dépend de l'entrepôt choisi
    assert acts["confirm_order"]["allowed_by_policy"] is False and acts["confirm_order"]["transition_valid"] is False
    assert acts["ship_order"]["transition_valid"] is False
    assert tuple(_erp_status(erp, "SO-20003")) == tuple(before)
