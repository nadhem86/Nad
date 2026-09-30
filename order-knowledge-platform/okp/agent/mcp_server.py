"""Serveur MCP (Model Context Protocol, SDK Python `mcp` v2) exposant les outils agents.

Authentification : la clé API est lue par le PROCESSUS SERVEUR (variable OKP_API_KEY) au
démarrage ; le principal et ses permissions (canal "agent") sont donc fixés côté serveur
et ne peuvent pas être modifiés par le client MCP ni par le contenu d'un prompt.

Lancement (stdio) :  OKP_API_KEY=demo-analyst-key python -m okp mcp
"""

from __future__ import annotations

import os
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer

from okp.agent.tools import AGENT_INSTRUCTIONS, TOOL_SPECS, AgentToolbox
from okp.security.policy import Principal
from okp.services import Platform

Concept = Literal["Customer", "Order", "OrderLine", "Product", "Warehouse", "Supplier"]
ActionName = Literal["confirm_order", "cancel_order", "assign_warehouse", "ship_order", "deliver_order"]


def build_mcp_server(platform: Platform, principal: Principal) -> MCPServer:
    box = AgentToolbox(platform)
    principal = principal.on_channel("agent")
    server = MCPServer(name="order-knowledge-platform", instructions=AGENT_INSTRUCTIONS,
                       version=platform.registry.fingerprint)

    def call(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        return box.call(principal, tool, {k: v for k, v in args.items() if v is not None})

    d = {n: desc for n, (_, desc) in TOOL_SPECS.items()}

    @server.tool(name="get_definition", description=d["get_definition"])
    def get_definition(term: str) -> dict[str, Any]:
        return call("get_definition", {"term": term})

    @server.tool(name="resolve_entity", description=d["resolve_entity"])
    def resolve_entity(query: str, concept: Concept | None = None) -> dict[str, Any]:
        return call("resolve_entity", {"query": query, "concept": concept})

    @server.tool(name="get_entity", description=d["get_entity"])
    def get_entity(entity_id: str, include_history: bool = False) -> dict[str, Any]:
        return call("get_entity", {"entity_id": entity_id, "include_history": include_history})

    @server.tool(name="get_relations", description=d["get_relations"])
    def get_relations(entity_id: str, depth: int = 1, include_inferred: bool = True,
                      predicates: list[str] | None = None,
                      direction: Literal["both", "out", "in"] = "both") -> dict[str, Any]:
        return call("get_relations", {"entity_id": entity_id, "depth": depth, "include_inferred": include_inferred,
                                      "predicates": predicates, "direction": direction})

    @server.tool(name="analyze_supplier_impact", description=d["analyze_supplier_impact"])
    def analyze_supplier_impact(supplier: str, open_orders_only: bool = True) -> dict[str, Any]:
        return call("analyze_supplier_impact", {"supplier": supplier, "open_orders_only": open_orders_only})

    @server.tool(name="compute_metric", description=d["compute_metric"])
    def compute_metric(metric: str, group_by: list[str] | None = None, filters: dict[str, Any] | None = None,
                       period_from: str | None = None, period_to: str | None = None) -> dict[str, Any]:
        return call("compute_metric", {"metric": metric, "group_by": group_by or [], "filters": filters or {},
                                       "period_from": period_from, "period_to": period_to})

    @server.tool(name="list_available_actions", description=d["list_available_actions"])
    def list_available_actions(order_id: str) -> dict[str, Any]:
        return call("list_available_actions", {"order_id": order_id})

    @server.tool(name="execute_action", description=d["execute_action"])
    def execute_action(action: ActionName, order_id: str, idempotency_key: str, reason: str | None = None,
                       warehouse_id: str | None = None, tracking_number: str | None = None) -> dict[str, Any]:
        return call("execute_action", {"action": action, "order_id": order_id, "idempotency_key": idempotency_key,
                                       "reason": reason, "warehouse_id": warehouse_id,
                                       "tracking_number": tracking_number})

    return server


def main() -> None:
    from okp.config import Settings

    platform = Platform(Settings.from_env())
    principal = platform.authenticate(os.environ.get("OKP_API_KEY"), "agent")
    build_mcp_server(platform, principal).run("stdio")
