"""Interface en ligne de commande : `python -m okp <commande>` (ou `okp` si installé).

La CLI est un outil d'exploitation local : l'identité est choisie par --as (principal
déclaré) et la politique d'accès reste appliquée (canal "cli")."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="okp", description="Order Knowledge Platform")
    ap.add_argument("--as", dest="principal", default="alice.admin", help="principal (défaut : alice.admin)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="créer/migrer la base")
    g = sub.add_parser("generate-demo", help="(ré)générer les sources fictives")
    g.add_argument("--force", action="store_true")
    i = sub.add_parser("ingest", help="importer les sources")
    i.add_argument("--mode", choices=["full", "incremental"], default="incremental")
    sub.add_parser("sync", help="traiter la file de resynchronisation (outbox)")
    sub.add_parser("build-ontology", help="générer generated/ontology.ttl et shapes.ttl")
    sub.add_parser("validate", help="valider le knowledge graph (SHACL)")
    sub.add_parser("reconcile", help="contrôler la cohérence entre couches")
    sub.add_parser("audit-verify", help="vérifier l'intégrité du journal d'audit")
    e = sub.add_parser("export-rdf", help="exporter le knowledge graph en TriG")
    e.add_argument("--out", default="data/kg.trig")
    d = sub.add_parser("define", help="définition d'un terme")
    d.add_argument("term")
    r = sub.add_parser("resolve", help="résoudre une entité")
    r.add_argument("query")
    en = sub.add_parser("entity", help="lire une entité")
    en.add_argument("id")
    rel = sub.add_parser("relations", help="relations d'une entité")
    rel.add_argument("id")
    rel.add_argument("--depth", type=int, default=1)
    m = sub.add_parser("metric", help="calculer une métrique")
    m.add_argument("name")
    m.add_argument("--by", action="append", default=[])
    m.add_argument("--filter", action="append", default=[], help="axe=valeur[,valeur]")
    m.add_argument("--from", dest="date_from")
    m.add_argument("--to", dest="date_to")
    a = sub.add_parser("actions", help="actions disponibles sur une commande")
    a.add_argument("order")
    x = sub.add_parser("act", help="exécuter une action")
    x.add_argument("action")
    x.add_argument("order")
    x.add_argument("--key", required=True, help="clé d'idempotence")
    x.add_argument("--param", action="append", default=[], help="nom=valeur")
    s = sub.add_parser("serve", help="lancer l'API REST")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    sub.add_parser("mcp", help="lancer le serveur MCP (stdio), identité = OKP_API_KEY")
    dm = sub.add_parser("demo", help="scénario de démonstration de bout en bout")
    dm.add_argument("--no-reset", action="store_true")
    args = ap.parse_args(argv)

    from okp.config import Settings
    settings = Settings.from_env()

    if args.cmd == "generate-demo":
        from okp.demo.generate import generate
        if (settings.sources_dir / "erp.sqlite").exists() and not args.force:
            print("sources déjà présentes (utiliser --force)")
            return 1
        _print(generate(settings.sources_dir))
        return 0
    if args.cmd == "build-ontology":
        from okp.ontology.build import write_artifacts
        from okp.registry import Registry
        for p in write_artifacts(Registry(settings.models_dir).glossary, Path(__file__).parent.parent / "generated"):
            print(p)
        return 0
    if args.cmd == "serve":
        import uvicorn
        uvicorn.run("okp.api.app:create_app", factory=True, host=args.host, port=args.port)
        return 0
    if args.cmd == "mcp":
        from okp.agent.mcp_server import main as mcp_main
        mcp_main()
        return 0
    if args.cmd == "demo":
        from okp.demo.scenario import run_demo
        t = run_demo(settings, reset=not args.no_reset)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        (settings.data_dir / "demo_transcript.json").write_text(json.dumps(t, indent=2, ensure_ascii=False, default=str))
        return 0

    from okp.services import Platform
    pf = Platform(settings)
    if args.cmd == "init":
        print(f"base prête : {settings.db_path}")
        return 0
    who = pf.auth.principal(args.principal, "cli")
    if args.cmd == "ingest":
        _print(pf.run_ingestion(who, args.mode))
    elif args.cmd == "sync":
        _print(pf.process_outbox(who))
    elif args.cmd == "validate":
        rep = pf.validate(who)
        _print(rep)
        return 0 if rep["conforms"] else 2
    elif args.cmd == "reconcile":
        rep = pf.reconcile(who)
        _print(rep)
        return 0 if rep["consistent"] else 2
    elif args.cmd == "audit-verify":
        rep = pf.audit.verify()
        _print(rep)
        return 0 if rep["valid"] else 2
    elif args.cmd == "export-rdf":
        from okp.ontology.validate import export_dataset
        pf.policy.require(who, "audit.read")
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        export_dataset(pf.conn, pf.registry.glossary).serialize(args.out, format="trig")
        print(args.out)
    elif args.cmd == "define":
        _print(pf.define(who, args.term))
    elif args.cmd == "resolve":
        _print(pf.resolve_entity(who, args.query))
    elif args.cmd == "entity":
        _print(pf.get_entity(who, args.id))
    elif args.cmd == "relations":
        _print(pf.relations(who, args.id, args.depth))
    elif args.cmd == "metric":
        filters = {}
        for f in args.filter:
            k, _, v = f.partition("=")
            filters[k] = v.split(",")
        period = {k: v for k, v in (("from", args.date_from), ("to", args.date_to)) if v} or None
        _print(pf.query_metric(who, args.name, args.by, filters, period))
    elif args.cmd == "actions":
        _print(pf.available_actions(who, args.order))
    elif args.cmd == "act":
        params = dict(p.split("=", 1) for p in args.param)
        _print(pf.execute_action(who, args.action, {"order_id": args.order, "idempotency_key": args.key, **params}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
