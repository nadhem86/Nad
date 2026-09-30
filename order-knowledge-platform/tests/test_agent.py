"""Interface agents : outils typés, provenance, ambiguïtés, absence de données, contenus non
fiables, et serveur MCP réel (transport en mémoire du SDK)."""

import asyncio

from conftest import canonical

from okp.agent.mcp_server import build_mcp_server
from okp.agent.tools import AgentToolbox, tool_schemas


def test_tool_catalog_is_typed():
    names = {t["name"] for t in tool_schemas()}
    assert names == {"get_definition", "resolve_entity", "get_entity", "get_relations", "analyze_supplier_impact",
                     "compute_metric", "list_available_actions", "execute_action"}
    for t in tool_schemas():
        assert t["input_schema"]["type"] == "object" and t["description"]


def test_definitions_come_only_from_models(pf, who):
    box = AgentToolbox(pf)
    p = who("farid.analyst")
    r = box.call(p, "get_definition", {"term": "Client"})
    assert r["status"] == "ok" and r["data"]["matches"][0]["name"] == "Customer"
    assert r["data"]["matches"][0]["ambiguities"]
    assert box.call(p, "get_definition", {"term": "EBITDA ajusté"})["status"] == "not_found"
    assert box.call(p, "get_definition", {"term": "ventes"})["status"] == "ambiguous"


def test_ambiguous_question_requires_clarification(pf, who):
    r = AgentToolbox(pf).call(who("farid.analyst"), "compute_metric", {"metric": "ventes"})
    assert r["status"] == "ambiguous"
    assert {c["name"] for c in r["data"]["candidates"]} == {"order_count", "quantity_sold", "revenue"}
    assert "value" not in r["data"]


def test_ambiguous_entity(pf, who):
    r = AgentToolbox(pf).call(who("bruno.manager"), "resolve_entity", {"query": "Lille"})
    assert r["status"] == "ambiguous" and len(r["data"]["candidates"]) >= 2


def test_unknown_and_no_data(pf, who):
    box = AgentToolbox(pf)
    p = who("farid.analyst")
    assert box.call(p, "compute_metric", {"metric": "NPS client"})["status"] == "unknown_metric"
    nd = box.call(p, "compute_metric", {"metric": "revenue", "period_from": "2031-01-01"})
    assert nd["status"] == "no_data" and nd["data"]["value"] is None and nd["guidance"]
    assert box.call(p, "get_entity", {"entity_id": "ORD-DOESNOTEXIST"})["status"] == "not_found"


def test_results_distinguish_observed_and_inferred_with_provenance(pf, who):
    box = AgentToolbox(pf)
    r = box.call(who("bruno.manager"), "get_relations", {"entity_id": canonical(pf, "SO-10001"), "direction": "out", "depth": 3})
    data = r["data"]
    assert data["observed"] and data["inferred"]
    assert all(e["provenance"]["kind"] == "observed" and e["provenance"]["source_id"] for e in data["observed"])
    assert all(e["provenance"]["rule_id"] and e["provenance"]["derived_from"] for e in data["inferred"])
    m = box.call(who("farid.analyst"), "compute_metric", {"metric": "CA", "group_by": ["time.month"]})
    assert m["status"] == "ok" and m["data"]["provenance"]["kind"] == "computed" and m["data"]["resolved_from"] == "CA"


def test_source_text_is_data_not_instruction(pf, who, erp):
    box = AgentToolbox(pf)
    p = who("bruno.manager")
    before = pf.conn.execute("SELECT COUNT(*) FROM action_log").fetchone()[0]
    r = box.call(p, "get_entity", {"entity_id": canonical(pf, "C003")})
    note = r["data"]["properties"]["note"][0]["value"]
    assert "IGNORE" in note["untrusted_text"] and "DONNÉE" in note["notice"]
    # Lire le contenu n'a déclenché aucune action ; aucune commande du client n'a changé.
    assert pf.conn.execute("SELECT COUNT(*) FROM action_log").fetchone()[0] == before
    assert erp.execute("SELECT COUNT(*) FROM ERP_ORDER WHERE CUST_NO='C003' AND STATUS_CD='CAN' AND CANCELLED_TS > '2026-07-01'").fetchone()[0] == 0
    # Et même si l'agent obéissait, l'annulation lui est refusée côté serveur.
    denied = box.call(p, "execute_action", {"action": "cancel_order", "order_id": "SO-20004", "reason": "injection",
                                            "idempotency_key": "k-inject-01"})
    assert denied["status"] == "forbidden"


def test_agent_cannot_widen_permissions_via_arguments(pf, who):
    box = AgentToolbox(pf)
    r = box.call(who("chloe.rep", "api"), "compute_metric", {"metric": "revenue", "role": "admin"})
    assert r["status"] == "error" and r["error"]["code"] == "VALIDATION_ERROR"
    r = box.call(who("chloe.rep", "api"), "execute_action",
                 {"action": "confirm_order", "order_id": "SO-20001", "idempotency_key": "k-agent-tool-1", "reason": "x"})
    assert r["status"] == "error"  # paramètre hors contrat


def test_agent_executes_authorized_action(pf, who):
    box = AgentToolbox(pf)
    p = who("chloe.rep")
    acts = box.call(p, "list_available_actions", {"order_id": "SO-20001"})
    assert {a["action"]: a["executable"] for a in acts["data"]["actions"]}["confirm_order"] is True
    r = box.call(p, "execute_action", {"action": "confirm_order", "order_id": "SO-20001", "idempotency_key": "k-agent-ok-1"})
    assert r["status"] == "ok" and r["data"]["new_status"] == "confirmed"
    assert pf.audit.events(1, "action.confirm_order")[0]["channel"] == "agent"


def test_mcp_server_end_to_end(pf, who):
    from mcp import Client

    server = build_mcp_server(pf, who("farid.analyst"))

    async def scenario():
        async with Client(server) as client:
            tools = {t.name for t in (await client.list_tools()).tools}
            ok = (await client.call_tool("compute_metric", {"metric": "gross_margin"})).structured_content
            denied = (await client.call_tool("execute_action", {"action": "confirm_order", "order_id": "SO-20001",
                                                                 "idempotency_key": "k-mcp-00001"})).structured_content
            return tools, ok, denied

    tools, ok, denied = asyncio.run(scenario())
    assert "execute_action" in tools and "compute_metric" in tools
    assert ok["status"] == "ok" and ok["data"]["metric"] == "gross_margin"
    assert denied["status"] == "forbidden"
