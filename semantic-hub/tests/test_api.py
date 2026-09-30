"""API REST : authentification, codes HTTP, parité avec le toolkit."""

import pytest
from fastapi.testclient import TestClient

from hub.api import create_app
from tests.conftest import iri_of


@pytest.fixture
def client(hub):
    return TestClient(create_app(hub))


def H(token):
    return {"Authorization": f"Bearer {token}"}


def test_requires_authentication(client):
    assert client.get("/definitions", params={"term": "Commande"}).status_code == 401
    assert client.get("/definitions", params={"term": "Commande"}, headers=H("faux")).status_code == 401


def test_health_and_definitions(client):
    h = client.get("/health").json()
    assert h["gateways"] == {"vtiger": "simulated", "wms": "simulated"} and h["projection_stale"] is False
    r = client.get("/definitions", params={"term": "marge"}, headers=H("demo-analyste"))
    assert r.status_code == 200 and r.json()["status"] == "ambiguous"


def test_metrics_endpoint(client):
    r = client.post("/metrics/query", headers=H("demo-analyste"),
                    json={"metrics": ["chiffre_affaires"], "period_start": "2026-07-01", "period_end": "2026-09-30"})
    assert r.status_code == 200
    assert r.json()["data"]["chiffre_affaires"]["rows"][0]["value"] == pytest.approx(4070.4)
    r = client.post("/metrics/query", headers=H("demo-commercial-nord"), json={"metrics": ["marge"]})
    assert r.status_code == 403


def test_actions_endpoint_authorized_forbidden_and_conflict(client, hub):
    so106 = iri_of(hub, "SO106")
    r = client.post("/actions/confirmer", headers={**H("demo-logisticien-lille"), "Idempotency-Key": "api-0"},
                    json={"commande": so106})
    assert r.status_code == 403
    r = client.post("/actions/confirmer", headers={**H("demo-commercial-nord"), "Idempotency-Key": "api-1"},
                    json={"commande": so106})
    assert r.status_code == 200 and r.json()["data"]["statut_apres"] == "confirmee"
    r = client.post("/actions/confirmer", headers={**H("demo-commercial-nord"), "Idempotency-Key": "api-2"},
                    json={"commande": so106})
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_TRANSITION"
    r = client.post("/actions/confirmer", headers=H("demo-commercial-nord"), json={"commande": so106})
    assert r.status_code == 422  # en-tête Idempotency-Key obligatoire


def test_entity_and_audit_endpoints(client, hub):
    r = client.get("/entities", params={"iri": iri_of(hub, "CON1")}, headers=H("demo-analyste"))
    mobile = [f for f in r.json()["data"]["observed"] if f["predicate"] == "nad:mobile"][0]
    assert mobile["masked"] == "pii"
    assert client.get("/audit", headers=H("demo-analyste")).status_code == 403
    a = client.get("/audit", headers=H("demo-admin")).json()
    assert a["chain_valid"] is True and a["entries"]


def test_openapi_documents_endpoints(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert {"/definitions", "/entities/resolve", "/metrics/query", "/actions/{action}", "/orders/actions"} <= set(paths)
