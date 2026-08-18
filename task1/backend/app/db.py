"""
SQLite schema and connection helper for the agentic vendor compliance
register.

WAL mode is enabled so concurrent readers/writers (behavior 9: two runs
at once stay two runs) don't block each other on every write - combined
with explicit BEGIN IMMEDIATE transactions in state_machine.py for the
specific operations that need a real mutual-exclusion guarantee (claiming
a stage, claiming the active-run lock for a pile).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path("data/vendor_register.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS piles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    watch_dir TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    pile_id TEXT NOT NULL REFERENCES piles(id),
    filename TEXT NOT NULL,
    doc_type TEXT,
    source_path TEXT NOT NULL,
    content_text TEXT,
    content_hash TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    UNIQUE(pile_id, content_hash)
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    pile_id TEXT NOT NULL REFERENCES piles(id),
    run_type TEXT NOT NULL,
    status TEXT NOT NULL,
    triggered_by TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS run_stages (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    stage_name TEXT NOT NULL,
    stage_order INTEGER NOT NULL,
    status TEXT NOT NULL,
    decision TEXT,
    result_json TEXT,
    error_message TEXT,
    locked_by TEXT,
    locked_at TEXT,
    started_at TEXT,
    completed_at TEXT,
    cost_cents REAL NOT NULL DEFAULT 0,
    duration_ms INTEGER,
    UNIQUE(run_id, stage_name)
);

CREATE TABLE IF NOT EXISTS active_run_locks (
    pile_id TEXT NOT NULL,
    run_type TEXT NOT NULL,
    run_id TEXT NOT NULL,
    locked_at TEXT NOT NULL,
    PRIMARY KEY (pile_id, run_type)
);

CREATE TABLE IF NOT EXISTS facts (
    id TEXT PRIMARY KEY,
    pile_id TEXT NOT NULL,
    document_id TEXT NOT NULL REFERENCES documents(id),
    fact_type TEXT NOT NULL,
    fact_json TEXT NOT NULL,
    source_excerpt TEXT,
    extracted_in_run_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rules (
    id TEXT PRIMARY KEY,
    pile_id TEXT NOT NULL,
    rule_text TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS findings (
    id TEXT PRIMARY KEY,
    pile_id TEXT NOT NULL,
    rule_id TEXT,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    severity TEXT,
    source_document_ids TEXT NOT NULL,
    source_excerpts TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_in_run_id TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deliverable_versions (
    id TEXT PRIMARY KEY,
    pile_id TEXT NOT NULL,
    version_number INTEGER NOT NULL,
    content_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    change_summary TEXT,
    created_in_run_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    UNIQUE(pile_id, version_number)
);

CREATE TABLE IF NOT EXISTS change_log (
    id TEXT PRIMARY KEY,
    pile_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    description TEXT NOT NULL,
    source_document_id TEXT,
    run_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS embeddings (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id),
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    vector_json TEXT NOT NULL
);
"""


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(
        str(db_path), timeout=30.0, isolation_level=None, check_same_thread=False
    )
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(SCHEMA)
    return conn
