"""Configuration issue des variables d'environnement (voir .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEV_AUDIT_KEY = "dev-only-insecure-audit-key"


@dataclass(frozen=True)
class Settings:
    env: str
    data_dir: Path
    db_path: Path
    sources_dir: Path
    models_dir: Path
    principals_file: Path
    audit_hmac_key: bytes

    @classmethod
    def from_env(cls, **overrides) -> "Settings":
        env = overrides.get("env") or os.getenv("OKP_ENV", "dev")
        data_dir = Path(overrides.get("data_dir") or os.getenv("OKP_DATA_DIR", PROJECT_ROOT / "data"))
        db_path = Path(overrides.get("db_path") or os.getenv("OKP_DB_PATH", data_dir / "okp.sqlite"))
        sources_dir = Path(overrides.get("sources_dir") or os.getenv("OKP_SOURCES_DIR", data_dir / "sources"))
        models_dir = Path(overrides.get("models_dir") or os.getenv("OKP_MODELS_DIR", PROJECT_ROOT / "models"))
        principals_file = Path(
            overrides.get("principals_file")
            or os.getenv("OKP_PRINCIPALS_FILE", PROJECT_ROOT / "config" / "principals.demo.yaml")
        )
        key = overrides.get("audit_hmac_key") or os.getenv("OKP_AUDIT_HMAC_KEY")
        if not key or key == "change-me":
            if env == "prod":
                raise RuntimeError("OKP_AUDIT_HMAC_KEY doit être défini (secret) lorsque OKP_ENV=prod.")
            key = _DEV_AUDIT_KEY
        return cls(env, data_dir, db_path, sources_dir, models_dir, principals_file, key.encode())
