"""Accès SQLite et migrations versionnées (okp/migrations/NNN_*.sql)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def connect(db_path: Path | str) -> sqlite3.Connection:
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Applique les migrations manquantes, dans l'ordre, chacune dans une transaction."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    done = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = path.stem
        if version in applied:
            continue
        conn.execute("BEGIN")
        try:
            for stmt in _split_sql(path.read_text(encoding="utf-8")):
                conn.execute(stmt)
            conn.execute("INSERT INTO schema_migrations VALUES (?, ?)", (version, utcnow()))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        done.append(version)
    return done


def _split_sql(script: str) -> list[str]:
    """Découpe un script en instructions (gère les triggers BEGIN ... END;)."""
    statements, buf = [], ""
    for line in script.splitlines():
        if line.strip().startswith("--") and not buf.strip():
            continue
        buf += line + "\n"
        if sqlite3.complete_statement(buf):
            if buf.strip():
                statements.append(buf.strip())
            buf = ""
    if buf.strip():
        statements.append(buf.strip())
    return statements
