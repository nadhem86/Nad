"""Orchestration d'une ingestion : run tracé -> écriture des faits (transaction) -> inférences -> projections -> audit."""

from __future__ import annotations

import csv
import hashlib
import json
import uuid
from pathlib import Path

from .. import inference, projection
from ..db import transaction, utcnow
from .vtiger import VtigerIngestor
from .whatsapp import WhatsAppIngestor
from .wms import WmsIngestor


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    files = sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path]
    for f in files:
        h.update(f.read_bytes())
    return h.hexdigest()


def ingest(hub, source: str, path: str | Path, mode: str | None = None, actor: str = "system") -> dict:
    path = Path(path)
    conn, kg = hub.conn, hub.kg
    run_id = f"{source}-{uuid.uuid4().hex[:10]}"
    region = hub.settings.default_phone_region
    conn.execute("INSERT INTO ingest_run (run_id, source, mode, input_path, input_sha256, started_at) VALUES (?,?,?,?,?,?)",
                 (run_id, source, mode or "auto", str(path), _sha256(path), utcnow()))
    with transaction(conn):
        if source == "vtiger":
            data = json.loads(path.read_text(encoding="utf-8"))
            mode = mode or ("delta" if "updated" in data else "snapshot")
            ing = VtigerIngestor(kg, run_id, region)
            result = ing.ingest_delta(data) if mode == "delta" else ing.ingest_snapshot(data)
        elif source == "wms":
            mode = "snapshot"
            result = WmsIngestor(kg, run_id).ingest_dir(path)
        elif source == "whatsapp":
            mode = "partial"
            result = WhatsAppIngestor(kg, run_id, region).ingest_export(json.loads(path.read_text(encoding="utf-8")))
        elif source == "fx":
            mode = "reference"
            result = load_fx(conn, path)
        else:
            raise ValueError(f"source inconnue : {source}")
        result["inference"] = inference.run_all(kg, run_id)
    result["projection"] = projection.rebuild(kg, hub.settings.business_timezone)
    conn.execute("UPDATE ingest_run SET mode=?, finished_at=?, stats=? WHERE run_id=?",
                 (mode, utcnow(), json.dumps(result, default=str), run_id))
    hub.audit.record(actor=actor, roles=None, kind="ingest", action=f"ingest:{source}", target=str(path),
                     request={"mode": mode}, outcome="ok", detail={"run_id": run_id, "stats": result.get("stats"),
                                                                   "rejected": len(result.get("rejected", []))})
    result["run_id"] = run_id
    result["mode"] = mode
    return result


def load_fx(conn, path: Path) -> dict:
    n = 0
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            conn.execute("INSERT INTO fx_rate VALUES (?,?,?,?) ON CONFLICT(currency, day) DO UPDATE SET "
                         "rate_to_eur=excluded.rate_to_eur, source=excluded.source",
                         (r["currency"], r["date"], float(r["rate_to_eur"]), path.name))
            n += 1
    return {"stats": {"rates": n}, "rejected": []}


def load_demo(hub) -> list[dict]:
    d = hub.settings.demo_dir
    return [
        ingest(hub, "fx", d / "reference" / "fx_rates.csv"),
        ingest(hub, "vtiger", d / "vtiger" / "snapshot_2026-09-30.json"),
        ingest(hub, "wms", d / "wms"),
        ingest(hub, "whatsapp", d / "whatsapp" / "whatsapp-export.json"),
    ]
