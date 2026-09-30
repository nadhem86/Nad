"""API REST (FastAPI TestClient) : authentification, codes HTTP, parcours de démonstration."""

import pytest
from fastapi.testclient import TestClient

from okp.api.app import create_app

H = lambda key: {"X-API-Key": key}  # noqa: E731


@pytest.fixture()
def client(pf):
    return TestClient(create_app(pf))


def test_health_and_openapi(client):
    assert client.get("/health").json()["status"] == "ok"
    paths = client.get("/openapi.json").json()["paths"]
    assert "/metrics/query" in paths and "/agent/tools/{tool}" in paths


def test_requires_api_key(client):
    assert client.get("/me").status_code == 401
    assert client.get("/me", headers=H("nope")).status_code == 401
    me = client.get("/me", headers=H("demo-rep-idf-key")).json()
    assert me["id"] == "chloe.rep" and me["channel"] == "api"


def test_demo_path_over_http(client):
    order = client.get("/entities/resolve", params={"q": "SO-10001"}, headers=H("demo-manager-key")).json()
    oid = order["candidates"][0]["canonical_id"]
    rel = client.get(f"/entities/{oid}/relations", params={"direction": "out", "depth": 3}, headers=H("demo-manager-key"))
    assert rel.status_code == 200 and rel.json()["observed"]
    q = client.post("/metrics/query", json={"metric": "revenue", "group_by": ["customer.region"]}, headers=H("demo-analyst-key"))
    assert q.status_code == 200 and q.json()["rows"]
    assert client.post("/metrics/query", json={"metric": "ventes"}, headers=H("demo-analyst-key")).status_code == 400
    assert client.post("/metrics/query", json={"metric": "gross_margin"}, headers=H("demo-rep-idf-key")).status_code == 403
    fan = client.post("/metrics/query", json={"metric": "order_count", "group_by": ["product.sku"]}, headers=H("demo-analyst-key"))
    assert fan.status_code == 400 and fan.json()["code"] == "FAN_OUT"


def test_actions_over_http(client):
    ok = client.post("/orders/SO-20001/actions/confirm_order", json={}, headers={**H("demo-rep-idf-key"), "Idempotency-Key": "http-key-001"})
    assert ok.status_code == 200 and ok.json()["new_status"] == "confirmed"
    replay = client.post("/orders/SO-20001/actions/confirm_order", json={}, headers={**H("demo-rep-idf-key"), "Idempotency-Key": "http-key-001"})
    assert replay.json()["replayed"] is True
    denied = client.post("/orders/SO-20002/actions/confirm_order", json={}, headers={**H("demo-analyst-key"), "Idempotency-Key": "http-key-002"})
    assert denied.status_code == 403
    invalid = client.post("/orders/SO-20007/actions/cancel_order", json={"reason": "trop tard"}, headers={**H("demo-admin-key"), "Idempotency-Key": "http-key-003"})
    assert invalid.status_code == 409 and invalid.json()["code"] == "INVALID_TRANSITION"


def test_agent_routes_use_agent_channel(client):
    tools = client.get("/agent/tools", headers=H("demo-manager-key")).json()
    assert tools["principal"]["channel"] == "agent" and "field.read:pii" in tools["principal"]["denied_on_channel"]
    r = client.post("/agent/tools/execute_action", json={"arguments": {"action": "cancel_order", "order_id": "SO-20001",
                    "reason": "via agent", "idempotency_key": "http-agent-01"}}, headers=H("demo-manager-key"))
    assert r.json()["status"] == "forbidden"


def test_governance_endpoints(client):
    assert client.get("/governance/audit", headers=H("demo-admin-key")).json()["integrity"]["valid"]
    assert client.get("/governance/audit", headers=H("demo-analyst-key")).status_code == 403
    assert client.get("/governance/reconciliation", headers=H("demo-admin-key")).json()["consistent"]
