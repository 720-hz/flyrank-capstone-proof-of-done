"""SQLite schema + connection helper. Plain sqlite3, no ORM — same approach as
every prior capstone, so tests/ and the live API call the exact same lib functions."""
import sqlite3
from contextlib import contextmanager

from app.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    api_key_hash TEXT NOT NULL UNIQUE,
    is_admin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id INTEGER NOT NULL REFERENCES agents(id),
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    agent_id INTEGER NOT NULL REFERENCES agents(id),
    claim_text TEXT NOT NULL,
    evidence_type TEXT NOT NULL CHECK (evidence_type IN ('file','url','text','command')),
    evidence_payload TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','verifying','verified','failed','suspicious',
                           'needs_llm_review','reviewing')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    resolved_at TEXT,
    claimed_at TEXT
);

CREATE TABLE IF NOT EXISTS verification_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id INTEGER NOT NULL REFERENCES claims(id),
    method TEXT NOT NULL CHECK (method IN ('file_hash','url_check','llm_review','sla_sweep')),
    outcome TEXT NOT NULL CHECK (outcome IN ('pass','fail','inconclusive')),
    detail TEXT,
    checked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id INTEGER NOT NULL REFERENCES claims(id),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('plausible','suspicious','insufficient_evidence')),
    confidence REAL NOT NULL,
    reasoning TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_micro_cents INTEGER NOT NULL DEFAULT 0,
    raw_response TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    pdf_path TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claim_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running','completed','failed')),
    total INTEGER NOT NULL DEFAULT 0,
    processed INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    last_error TEXT
);
"""


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str | None = None) -> None:
    conn = get_connection(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def db(db_path: str | None = None):
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
