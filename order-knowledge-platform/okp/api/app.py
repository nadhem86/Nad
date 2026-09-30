"""API REST typée (FastAPI). Documentation interactive : /docs (OpenAPI : /openapi.json).

Authentification : en-tête `X-API-Key`. Les routes /agent/* imposent le canal "agent"
(permissions plafonnées pour les agents IA) ; les autres routes utilisent le canal "api".
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from okp.agent.tools import AGENT_INSTRUCTIONS, AgentToolbox, tool_schemas
from okp.config import Settings
from okp.operations.actions import ActionError
from okp.security.policy import AccessError, Principal
from okp.semantic.engine import MetricError
from okp.services import Platform

STATUS = {"UNAUTHENTICATED": 401, "FORBIDDEN": 403, "NOT_FOUND": 404, "INVALID_TRANSITION": 409,
          "PRECONDITION_FAILED": 422, "CONFLICT": 409, "IDEMPOTENCY_CONFLICT": 409, "VALIDATION_ERROR": 422,
          "SOURCE_UNAVAILABLE": 503, "FAN_OUT": 400, "INVALID_DIMENSION": 400, "INVALID_PERIOD": 400,
          "UNKNOWN_METRIC": 404}


class MetricQuery(BaseModel):
    metric: str = Field(..., description="Nom ou synonyme non ambigu d'une métrique.")
    group_by: list[str] = Field(default_factory=list, examples=[["customer.region", "time.month"]])
    filters: dict[str, str | list[str]] = Field(default_factory=dict, examples=[{"product.category": ["MOBILIER"]}])
    period: dict[str, str] | None = Field(None, examples=[{"from": "2026-01-01", "to": "2026-03-31"}])


class ToolCall(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


def create_app(platform: Platform | None = None) -> FastAPI:
    pf = platform or Platform(Settings.from_env())
    toolbox = AgentToolbox(pf)
    app = FastAPI(title="Order Knowledge Platform", version="0.1.0",
                  description="Ontologie, knowledge graph, modèle sémantique et actions métier (démonstration). "
                              f"Versions des modèles : {pf.registry.versions}")
    app.state.platform = pf

    @app.exception_handler(AccessError)
    async def _access(_: Request, exc: AccessError):
        return JSONResponse(status_code=STATUS.get(exc.code, 403), content={"code": exc.code, "message": exc.message})

    @app.exception_handler(ActionError)
    async def _action(_: Request, exc: ActionError):
        return JSONResponse(status_code=STATUS.get(exc.code, 400), content=exc.as_dict())

    @app.exception_handler(MetricError)
    async def _metric(_: Request, exc: MetricError):
        return JSONResponse(status_code=STATUS.get(exc.code, 400),
                            content={"code": exc.code, "message": exc.message, "details": exc.details})

    def api_principal(x_api_key: str | None = Header(None)) -> Principal:
        return pf.authenticate(x_api_key, "api")

    def agent_principal(x_api_key: str | None = Header(None)) -> Principal:
        return pf.authenticate(x_api_key, "agent")

    # ------------------------------------------------------------------ santé & modèles
    @app.get("/health")
    def health():
        return {"status": "ok", "model_versions": pf.model_versions()}

    @app.get("/me")
    def me(p: Principal = Depends(api_principal)):
        return pf.whoami(p)

    @app.get("/model/catalog")
    def catalog(p: Principal = Depends(api_principal)):
        return pf.catalog()

    @app.get("/model/definitions/{term}")
    def definition(term: str, p: Principal = Depends(api_principal)):
        return pf.define(p, term)

    @app.get("/model/metrics/{metric}")
    def metric_definition(metric: str, p: Principal = Depends(api_principal)):
        return pf.describe_metric(p, metric)

    # ------------------------------------------------------------------ entités & graphe
    @app.get("/entities/resolve")
    def resolve(q: str = Query(..., min_length=1), concept: str | None = None, p: Principal = Depends(api_principal)):
        return pf.resolve_entity(p, q, concept)

    @app.get("/entities/{entity_id}")
    def entity(entity_id: str, history: bool = False, p: Principal = Depends(api_principal)):
        return pf.get_entity(p, entity_id, history)

    @app.get("/entities/{entity_id}/relations")
    def relations(entity_id: str, depth: int = Query(1, ge=1, le=3), include_inferred: bool = True,
                  direction: str = Query("both", pattern="^(both|out|in)$"), p: Principal = Depends(api_principal)):
        return pf.relations(p, entity_id, depth, include_inferred, direction=direction)

    @app.get("/orders/{order_ref}")
    def order(order_ref: str, p: Principal = Depends(api_principal)):
        return pf.order_overview(p, order_ref)

    @app.get("/graph/supplier-impact/{supplier}")
    def impact(supplier: str, open_only: bool = True, p: Principal = Depends(api_principal)):
        return pf.supplier_impact(p, supplier, open_only)

    # ------------------------------------------------------------------ métriques
    @app.post("/metrics/query")
    def metrics(q: MetricQuery, p: Principal = Depends(api_principal)):
        r = pf.query_metric(p, q.metric, q.group_by, q.filters, q.period)
        if r["status"] in ("ambiguous", "unknown_metric"):
            return JSONResponse(status_code=400 if r["status"] == "ambiguous" else 404, content=r)
        return r

    # ------------------------------------------------------------------ actions
    @app.get("/orders/{order_ref}/actions")
    def actions(order_ref: str, p: Principal = Depends(api_principal)):
        return pf.available_actions(p, order_ref)

    @app.post("/orders/{order_ref}/actions/{action}")
    def execute(order_ref: str, action: str, body: dict[str, Any],
                idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
                p: Principal = Depends(api_principal)):
        params = {**body, "order_id": order_ref}
        if idempotency_key:
            params["idempotency_key"] = idempotency_key
        return pf.execute_action(p, action, params)

    # ------------------------------------------------------------------ gouvernance
    @app.get("/governance/audit")
    def audit(limit: int = Query(50, le=500), event_type: str | None = None, p: Principal = Depends(api_principal)):
        return pf.audit_events(p, limit, event_type)

    @app.get("/governance/validation")
    def validation(p: Principal = Depends(api_principal)):
        return pf.validate(p)

    @app.get("/governance/reconciliation")
    def reconciliation(p: Principal = Depends(api_principal)):
        return pf.reconcile(p)

    @app.get("/governance/ingestion-issues")
    def issues(p: Principal = Depends(api_principal)):
        return pf.ingestion_issues(p)

    @app.post("/ingestion/run")
    def ingest(mode: str = Query("incremental", pattern="^(full|incremental)$"), p: Principal = Depends(api_principal)):
        return pf.run_ingestion(p, mode)

    # ------------------------------------------------------------------ outils agents
    @app.get("/agent/tools")
    def agent_tools(p: Principal = Depends(agent_principal)):
        return {"instructions": AGENT_INSTRUCTIONS, "tools": tool_schemas(), "principal": pf.whoami(p)}

    @app.post("/agent/tools/{tool}")
    def agent_call(tool: str, call: ToolCall, p: Principal = Depends(agent_principal)):
        if tool not in {t["name"] for t in tool_schemas()}:
            raise HTTPException(404, "outil inconnu")
        return toolbox.call(p, tool, call.arguments)

    return app
