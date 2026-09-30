"""API REST typée (FastAPI). Documentation OpenAPI : /docs. Authentification : `Authorization: Bearer <jeton>`.

Toutes les routes passent par AgentToolkit, donc par les mêmes contrôles d'accès, calculs et règles de
provenance que le serveur MCP.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .security import AccessDenied, Principal
from .services import Hub

HTTP_BY_CODE = {"FORBIDDEN": 403, "OUT_OF_SCOPE": 403, "NOT_FOUND": 404, "INVALID_TRANSITION": 409,
                "IDEMPOTENCY_CONFLICT": 409, "STOCK_INSUFFISANT": 409, "SOURCE_UNAVAILABLE": 503,
                "SYNC_PENDING": 202, "VALIDATION_ERROR": 422}


class Envelope(BaseModel):
    status: Literal["ok", "ambiguous", "not_found", "no_data", "forbidden", "error"]
    data: Any = None
    provenance: list[dict] | None = None
    warnings: list[Any] | None = None
    clarification: dict | None = None
    notices: list[str] | None = None
    error: dict | None = None


class MetricRequest(BaseModel):
    metrics: list[str] = Field(..., examples=[["chiffre_affaires", "marge"]])
    group_by: list[str] = Field(default_factory=list, examples=[["client.nom", "temps.trimestre"]])
    filters: dict[str, Any] = Field(default_factory=dict, examples=[{"client.region": "Nord"}])
    period_start: str | None = Field(None, examples=["2026-07-01"], description="date locale Europe/Paris incluse")
    period_end: str | None = Field(None, examples=["2026-09-30"])


class ActionRequest(BaseModel):
    commande: str = Field(..., description="IRI canonique de la commande")
    params: dict[str, Any] = Field(default_factory=dict, examples=[{"motif": "Client injoignable"}])


def create_app(hub: Hub | None = None) -> FastAPI:
    app = FastAPI(title="NAD Semantic Hub", version="0.1.0",
                  description="Ontologie, knowledge graph, métriques partagées et actions contrôlées (vTiger / WMS / WhatsApp).")
    app.state.hub = hub or Hub()

    def principal(request: Request, authorization: str | None = Header(None)) -> Principal:
        token = authorization.split(" ", 1)[1] if authorization and authorization.lower().startswith("bearer ") else None
        try:
            return request.app.state.hub.policy.authenticate(token)
        except AccessDenied as e:
            raise HTTPException(401, detail=str(e), headers={"WWW-Authenticate": "Bearer"})

    def reply(env: dict) -> JSONResponse:
        code = 200
        if env["status"] == "forbidden":
            code = 403
        elif env["status"] == "not_found" and env.get("error"):
            code = 404
        elif env["status"] == "error":
            code = HTTP_BY_CODE.get((env.get("error") or {}).get("code"), 400)
        return JSONResponse(Envelope(**env).model_dump(exclude_none=True), status_code=code)

    def tk(request: Request, p: Principal):
        return request.app.state.hub.toolkit(p)

    @app.get("/health")
    def health(request: Request):
        from .projection import is_stale

        h = request.app.state.hub
        return {"status": "ok", "glossary_version": h.glossary.version, "model_version": h.model.version,
                "projection_stale": is_stale(h.conn),
                "gateways": {k: ("simulated" if g.simulated else "live") for k, g in h.gateways.items()}}

    @app.get("/definitions", response_model=Envelope, summary="Définition d'un terme métier (concept, métrique, propriété, état)")
    def define(request: Request, term: str, p: Principal = Depends(principal)):
        return reply(tk(request, p).define(term))

    @app.get("/entities/resolve", response_model=Envelope, summary="Résoudre un nom ou un identifiant en IRI canonique")
    def resolve(request: Request, q: str, concept: str | None = None, p: Principal = Depends(principal)):
        return reply(tk(request, p).resolve_entity(q, concept))

    @app.get("/entities", response_model=Envelope, summary="Faits observés et inférés d'une entité, avec provenance")
    def entity(request: Request, iri: str, p: Principal = Depends(principal)):
        return reply(tk(request, p).get_entity(iri))

    @app.get("/entities/relations", response_model=Envelope)
    def relations(request: Request, iri: str, depth: int = Query(1, ge=1, le=3), p: Principal = Depends(principal)):
        return reply(tk(request, p).relations(iri, depth))

    @app.get("/entities/impact", response_model=Envelope, summary="Analyse d'impact (chemins déclarés dans le glossaire)")
    def impact(request: Request, iri: str, p: Principal = Depends(principal)):
        return reply(tk(request, p).impact(iri))

    @app.get("/metrics", response_model=Envelope, summary="Catalogue des métriques partagées")
    def catalog(request: Request, p: Principal = Depends(principal)):
        return reply(tk(request, p).metric_catalog())

    @app.post("/metrics/query", response_model=Envelope, summary="Calculer des métriques selon les définitions partagées")
    def metrics(request: Request, body: MetricRequest, p: Principal = Depends(principal)):
        return reply(tk(request, p).compute_metric(body.metrics, body.group_by, body.filters,
                                                    body.period_start, body.period_end))

    @app.get("/orders/actions", response_model=Envelope, summary="Actions disponibles sur une commande pour l'appelant")
    def actions(request: Request, commande: str, p: Principal = Depends(principal)):
        return reply(tk(request, p).list_actions(commande))

    @app.post("/actions/{action}", response_model=Envelope, summary="Exécuter une action métier contrôlée")
    def run_action(request: Request, action: str, body: ActionRequest,
                   idempotency_key: str = Header(..., alias="Idempotency-Key"), p: Principal = Depends(principal)):
        return reply(tk(request, p).execute_action(action, body.commande, idempotency_key, body.params))

    @app.get("/audit", summary="Journal d'audit (permission audit:read)")
    def audit(request: Request, n: int = 50, kind: str | None = None, p: Principal = Depends(principal)):
        h = request.app.state.hub
        if not h.policy.has(p, "audit:read"):
            raise HTTPException(403, "permission audit:read requise")
        ok, broken = h.audit.verify()
        return {"chain_valid": ok, "first_broken_seq": broken, "entries": h.audit.tail(n, kind)}

    return app


app = None  # instancié par `python -m hub.cli serve` (évite d'ouvrir la base à l'import)
