"""Assemblage des services, partagé par l'API REST, le serveur MCP, la CLI et les tests."""

from __future__ import annotations

from .audit import AuditLog
from .config import Settings
from .db import connect
from .gateways import SimulatedGateway, VtigerLiveGateway
from .glossary import load_glossary
from .kg import KnowledgeGraph
from .security import PolicyEngine
from .semantic import MetricEngine, SemanticModel


class Hub:
    def __init__(self, settings: Settings | None = None):
        from .operations import OrderService

        self.settings = settings or Settings()
        self.conn = connect(self.settings.db_path)
        self.glossary = load_glossary(self.settings)
        self.kg = KnowledgeGraph(self.conn, self.glossary)
        self.policy = PolicyEngine(self.settings.policies_path, self.settings.principals_file)
        self.audit = AuditLog(self.conn)
        self.model = SemanticModel(self.settings.model_path)
        self.metrics = MetricEngine(self.conn, self.model)
        self.gateways = self._gateways()
        self.orders = OrderService(self)

    def _gateways(self) -> dict:
        gws = {"vtiger": SimulatedGateway(self.conn, "vtiger"), "wms": SimulatedGateway(self.conn, "wms")}
        if self.settings.gateway_mode == "live":
            from .ingest.vtiger import VtigerWebserviceClient

            s = self.settings
            if not (s.vtiger_url and s.vtiger_username and s.vtiger_access_key):
                raise RuntimeError("NAD_GATEWAY_MODE=live exige NAD_VTIGER_URL, NAD_VTIGER_USERNAME, NAD_VTIGER_ACCESS_KEY")
            client = VtigerWebserviceClient(s.vtiger_url, s.vtiger_username, s.vtiger_access_key)
            client.login()
            gws["vtiger"] = VtigerLiveGateway(client)
        return gws

    def toolkit(self, principal):
        from .agent_tools import AgentToolkit

        return AgentToolkit(self, principal)

    def close(self) -> None:
        self.conn.close()
