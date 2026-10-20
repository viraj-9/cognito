"""SQLite persistence with explicit schema and request-scoped connections."""
from contextlib import contextmanager
import sqlite3
from pathlib import Path
from typing import Iterator

from .settings import settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  email TEXT NOT NULL COLLATE NOCASE UNIQUE,
  display_name TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  preferred_language TEXT NOT NULL DEFAULT 'auto',
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  csrf_token TEXT NOT NULL,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS progress (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  resource_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('started','completed')),
  score REAL CHECK(score IS NULL OR (score >= 0 AND score <= 1)),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  PRIMARY KEY(user_id, resource_id)
);
CREATE TABLE IF NOT EXISTS assessment_attempts (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  resource_id TEXT NOT NULL,
  score REAL NOT NULL CHECK(score >= 0 AND score <= 1),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_attempts_user_time ON assessment_attempts(user_id, created_at DESC);
CREATE TABLE IF NOT EXISTS recommendation_feedback (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  resource_id TEXT NOT NULL,
  helpful INTEGER NOT NULL CHECK(helpful IN (0,1)),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_feedback_user_resource ON recommendation_feedback(user_id, resource_id);
CREATE TABLE IF NOT EXISTS rate_limits (
  bucket TEXT NOT NULL,
  subject_hash TEXT NOT NULL,
  window_start INTEGER NOT NULL,
  hits INTEGER NOT NULL,
  PRIMARY KEY(bucket, subject_hash, window_start)
);
"""
CONVERSATIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  mode TEXT NOT NULL CHECK(mode IN ('casual','interview','interview_hr','interview_tech','english','roleplay')),
  settings_json TEXT NOT NULL DEFAULT '{}',
  messages_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','completed')),
  summary TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  ended_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_conversations_user_updated ON conversations(user_id, updated_at DESC);
"""
MODES_MIGRATION = """
PRAGMA foreign_keys=OFF;
CREATE TABLE IF NOT EXISTS conversations_new (
  id TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  mode TEXT NOT NULL CHECK(mode IN ('casual','interview','interview_hr','interview_tech','english','roleplay')),
  settings_json TEXT NOT NULL DEFAULT '{}',
  messages_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','completed')),
  summary TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  ended_at TEXT
);
INSERT INTO conversations_new SELECT * FROM conversations;
DROP TABLE conversations;
ALTER TABLE conversations_new RENAME TO conversations;
CREATE INDEX IF NOT EXISTS idx_conversations_user_updated ON conversations(user_id, updated_at DESC);
PRAGMA foreign_keys=ON;
"""
REPORTS_MIGRATION = """
ALTER TABLE conversations ADD COLUMN report_json TEXT;
ALTER TABLE conversations ADD COLUMN report_status TEXT NOT NULL DEFAULT 'none';
"""
MOCK_INTERVIEW_MIGRATION = """
CREATE TABLE conversations_new (
  id TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  mode TEXT NOT NULL CHECK(mode IN ('casual','interview','interview_hr','interview_tech','mock_interview','english','roleplay')),
  settings_json TEXT NOT NULL DEFAULT '{}',
  messages_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','completed')),
  summary TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  ended_at TEXT,
  report_json TEXT,
  report_status TEXT NOT NULL DEFAULT 'none'
);
INSERT INTO conversations_new SELECT * FROM conversations;
DROP TABLE conversations;
ALTER TABLE conversations_new RENAME TO conversations;
CREATE INDEX IF NOT EXISTS idx_conversations_user_updated ON conversations(user_id, updated_at DESC);
"""
MIGRATIONS = [("001_initial",SCHEMA),("002_conversations",CONVERSATIONS_SCHEMA),("003_modes",MODES_MIGRATION),("004_reports",REPORTS_MIGRATION),("005_mock_interview",MOCK_INTERVIEW_MIGRATION)]


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = path or settings.database_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=8, isolation_level="DEFERRED")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 8000")
    return conn


def initialize_database(path: Path | None = None) -> None:
    conn=connect(path)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))")
        for version,script in MIGRATIONS:
            already=conn.execute("SELECT 1 FROM schema_migrations WHERE version=?",(version,)).fetchone()
            if not already:
                conn.executescript("BEGIN IMMEDIATE;\n"+script+f"\nINSERT INTO schema_migrations(version) VALUES('{version}');\nCOMMIT;")
    finally:
        conn.close()


@contextmanager
def database() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
