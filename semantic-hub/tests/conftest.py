import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def hub(tmp_path, monkeypatch):
    """Hub sur une base neuve avec le jeu de démonstration importé."""
    monkeypatch.setenv("NAD_DB_PATH", str(tmp_path / "test.sqlite"))
    monkeypatch.setenv("NAD_GATEWAY_MODE", "simulated")
    from hub.ingest.pipeline import load_demo
    from hub.services import Hub

    h = Hub()
    load_demo(h)
    yield h
    h.close()


@pytest.fixture
def tk(hub):
    """Fabrique de toolkits par identifiant de principal."""
    return lambda pid: hub.toolkit(hub.policy.principal(pid))


def iri_of(hub, query, concept=None):
    admin = hub.toolkit(hub.policy.principal("alice.admin"))
    r = admin.resolve_entity(query, concept)
    assert r["status"] == "ok", r
    return r["data"]["entity"]["iri"]
