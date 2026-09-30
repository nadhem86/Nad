"""Interface en ligne de commande.

  python -m hub.cli init                      # crée la base (migrations)
  python -m hub.cli ingest-demo               # importe le jeu de démonstration
  python -m hub.cli ingest vtiger <fichier>   # snapshot ou delta vTiger (détecté automatiquement)
  python -m hub.cli ingest wms <dossier>
  python -m hub.cli ingest whatsapp <export.json>
  python -m hub.cli extract-vtiger <fichier>  # extrait un snapshot d'un vTiger RÉEL (NAD_VTIGER_*)
  python -m hub.cli validate                  # validation SHACL du graphe
  python -m hub.cli export-rdf <fichier.trig>
  python -m hub.cli audit-verify
  python -m hub.cli serve [--port 8000]       # API REST
  python -m hub.cli mcp                       # serveur MCP stdio (principal : NAD_MCP_TOKEN)
"""

from __future__ import annotations

import argparse
import json
import sys

from .services import Hub


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hub")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    p = sub.add_parser("ingest-demo"); p.add_argument("--if-empty", action="store_true")
    p = sub.add_parser("ingest"); p.add_argument("source", choices=["vtiger", "wms", "whatsapp", "fx"]); p.add_argument("path")
    p.add_argument("--mode", choices=["snapshot", "delta"])
    p = sub.add_parser("extract-vtiger"); p.add_argument("out")
    sub.add_parser("validate")
    p = sub.add_parser("export-rdf"); p.add_argument("out")
    sub.add_parser("audit-verify")
    p = sub.add_parser("serve"); p.add_argument("--host", default="127.0.0.1"); p.add_argument("--port", type=int, default=8000)
    sub.add_parser("mcp")
    a = ap.parse_args(argv)

    if a.cmd == "mcp":
        from .mcp_server import main as mcp_main
        mcp_main()
        return 0
    if a.cmd == "serve":
        import uvicorn

        from .api import create_app
        uvicorn.run(create_app(), host=a.host, port=a.port)
        return 0

    hub = Hub()
    if a.cmd == "init":
        print("base prête :", hub.settings.db_path)
    elif a.cmd == "ingest-demo":
        from .ingest.pipeline import load_demo
        if a.if_empty and hub.conn.execute("SELECT COUNT(*) FROM fact").fetchone()[0]:
            print("base déjà alimentée : import de démonstration ignoré")
            return 0
        for r in load_demo(hub):
            print(json.dumps({"run_id": r["run_id"], "mode": r["mode"], "stats": r["stats"], "rejected": r["rejected"],
                              "inference": r["inference"]}, ensure_ascii=False))
    elif a.cmd == "ingest":
        from .ingest.pipeline import ingest
        r = ingest(hub, a.source, a.path, a.mode)
        print(json.dumps(r, ensure_ascii=False, indent=1, default=str))
    elif a.cmd == "extract-vtiger":
        from .ingest.vtiger import VtigerWebserviceClient
        s = hub.settings
        if not (s.vtiger_url and s.vtiger_username and s.vtiger_access_key):
            print("NAD_VTIGER_URL / NAD_VTIGER_USERNAME / NAD_VTIGER_ACCESS_KEY requis", file=sys.stderr)
            return 2
        c = VtigerWebserviceClient(s.vtiger_url, s.vtiger_username, s.vtiger_access_key)
        c.login()
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(c.extract_snapshot(), f, ensure_ascii=False, indent=1)
        print("snapshot écrit :", a.out)
    elif a.cmd == "validate":
        from .rdf import validate
        ok, results = validate(hub.kg)
        print(json.dumps({"conforms": ok, "violations": results}, ensure_ascii=False, indent=1))
        return 0 if ok else 1
    elif a.cmd == "export-rdf":
        from .rdf import export_graph
        export_graph(hub.kg).serialize(a.out, format="trig")
        print("export :", a.out)
    elif a.cmd == "audit-verify":
        ok, seq = hub.audit.verify()
        print(json.dumps({"chain_valid": ok, "first_broken_seq": seq}))
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
