"""Configuration par variables d'environnement (voir .env.example). Aucun secret n'a de valeur par défaut."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    db_path: str = field(default_factory=lambda: os.environ.get("NAD_DB_PATH", str(ROOT / "var" / "hub.sqlite")))
    principals_file: str = field(
        default_factory=lambda: os.environ.get("NAD_PRINCIPALS_FILE", str(ROOT / "security" / "principals.demo.yaml"))
    )
    business_timezone: str = field(default_factory=lambda: os.environ.get("NAD_BUSINESS_TZ", "Europe/Paris"))
    default_phone_region: str = field(default_factory=lambda: os.environ.get("NAD_DEFAULT_PHONE_REGION", "FR"))
    # simulated : passerelles de démo (écriture dans sim_source_write) ; live : webservice vTiger réel.
    gateway_mode: str = field(default_factory=lambda: os.environ.get("NAD_GATEWAY_MODE", "simulated"))
    vtiger_url: str | None = field(default_factory=lambda: os.environ.get("NAD_VTIGER_URL"))
    vtiger_username: str | None = field(default_factory=lambda: os.environ.get("NAD_VTIGER_USERNAME"))
    vtiger_access_key: str | None = field(default_factory=lambda: os.environ.get("NAD_VTIGER_ACCESS_KEY"))

    glossary_path: Path = ROOT / "ontology" / "glossary.yaml"
    model_path: Path = ROOT / "semantic_model" / "model.yaml"
    lifecycle_path: Path = ROOT / "operations" / "order_lifecycle.yaml"
    policies_path: Path = ROOT / "security" / "policies.yaml"
    demo_dir: Path = ROOT / "data" / "demo"
