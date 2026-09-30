"""Fixtures : un environnement de démonstration est construit une fois (sources fictives +
import complet), puis copié pour chaque test afin que les tests restent indépendants."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from okp.config import PROJECT_ROOT, Settings
from okp.demo.generate import generate
from okp.services import Platform


def make_settings(root: Path) -> Settings:
    return Settings.from_env(env="dev", data_dir=root, db_path=root / "okp.sqlite", sources_dir=root / "sources",
                             models_dir=PROJECT_ROOT / "models",
                             principals_file=PROJECT_ROOT / "config" / "principals.demo.yaml",
                             audit_hmac_key="test-audit-key")


@pytest.fixture(scope="session")
def template_dir(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("template")
    generate(root / "sources")
    pf = Platform(make_settings(root))
    pf.run_ingestion(pf.auth.principal("ingest.bot"), "full")
    pf.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    pf.conn.close()
    return root


@pytest.fixture()
def env(template_dir, tmp_path) -> Path:
    root = tmp_path / "env"
    shutil.copytree(template_dir, root)
    return root


@pytest.fixture()
def pf(env) -> Platform:
    platform = Platform(make_settings(env))
    yield platform
    platform.conn.close()


@pytest.fixture()
def who(pf):
    return lambda pid, channel="api": pf.auth.principal(pid, channel)


@pytest.fixture()
def erp(env):
    conn = sqlite3.connect(env / "sources" / "erp.sqlite")
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def canonical(pf: Platform, source_id: str) -> str:
    return pf.conn.execute("SELECT canonical_id FROM id_map WHERE source_id=?", (source_id,)).fetchone()[0]


def current_facts(pf: Platform, subject: str, predicate: str) -> list[sqlite3.Row]:
    return pf.conn.execute("SELECT * FROM fact WHERE subject=? AND predicate=? AND superseded_at IS NULL "
                           "AND retracted_at IS NULL", (subject, predicate)).fetchall()
