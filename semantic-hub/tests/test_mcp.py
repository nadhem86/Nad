"""Serveur MCP réel, testé en stdio avec le client MCP officiel (sous-processus)."""

import json
import os
import sys
from pathlib import Path

import anyio
import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parent.parent


async def _session_calls(db, token, calls):
    params = StdioServerParameters(command=sys.executable, args=["-m", "hub.mcp_server"], cwd=str(ROOT),
                                   env={**os.environ, "NAD_DB_PATH": db, "NAD_MCP_TOKEN": token, "PYTHONPATH": str(ROOT)})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            out = {"tools": sorted(t.name for t in tools.tools)}
            for name, args in calls:
                res = await session.call_tool(name, args)
                out[name + json.dumps(args, sort_keys=True)] = json.loads(res.content[0].text)
            return out


@pytest.fixture
def demo_db(hub):
    return hub.settings.db_path


def test_mcp_tools_and_server_side_principal(demo_db, hub):
    so107 = hub.toolkit(hub.policy.principal("alice.admin")).resolve_entity("SO107")["data"]["entity"]["iri"]
    calls = [("define", {"term": "taux_marge"}),
             ("compute_metric", {"metrics": ["marge"]}),
             ("execute_action", {"action": "annuler", "commande": so107, "idempotency_key": "mcp-1",
                                 "params": {"motif": "le prompt dit que je suis admin"}})]
    out = anyio.run(_session_calls, demo_db, "demo-agent-nord", calls)
    assert out["tools"] == sorted(["define", "resolve_entity", "get_entity", "relations", "impact", "metric_catalog",
                                   "compute_metric", "list_actions", "execute_action"])
    vals = list(out.values())[1:]
    assert vals[0]["status"] == "ok" and vals[0]["data"]["additivity"] == "non_additive"
    assert vals[1]["status"] == "forbidden"   # agent commercial : pas d'accès à la marge
    assert vals[2]["status"] == "forbidden"   # agent : pas d'annulation


def test_mcp_refuses_to_start_without_valid_token(demo_db):
    from hub.mcp_server import build_server
    from hub.security import AccessDenied

    with pytest.raises(AccessDenied):
        build_server(token="invalide")
