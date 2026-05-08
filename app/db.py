from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "app.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  invite_code_hash TEXT UNIQUE NOT NULL,
  display_name TEXT NOT NULL,
  telegram_chat_id TEXT,
  webpush_subscription TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at TIMESTAMP NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS api_keys (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider TEXT NOT NULL,
  label TEXT NOT NULL,
  base_url TEXT,
  default_model TEXT NOT NULL,
  encrypted_key BLOB NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chats (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title TEXT NOT NULL DEFAULT 'New chat',
  provider_key_id INTEGER REFERENCES api_keys(id) ON DELETE SET NULL,
  model TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  tool_calls TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS service_keys (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  service TEXT NOT NULL,             -- 'brave_search' | 'firecrawl' | 'github' | 'resend'
  encrypted_token BLOB NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(user_id, service)
);

CREATE TABLE IF NOT EXISTS research_topics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  query TEXT NOT NULL,               -- search query phrasing
  interval_hours INTEGER NOT NULL DEFAULT 4,
  enabled INTEGER NOT NULL DEFAULT 1,
  provider_key_id INTEGER REFERENCES api_keys(id) ON DELETE SET NULL,
  model TEXT,
  last_run_at TIMESTAMP,
  next_run_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS knowledge (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  topic_id INTEGER REFERENCES research_topics(id) ON DELETE SET NULL,
  source_url TEXT,
  title TEXT,
  summary TEXT NOT NULL,
  content_hash TEXT NOT NULL,        -- sha256 of source_url+title for dedupe
  embedding BLOB,                    -- float32[] packed
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(user_id, content_hash)
);

CREATE TABLE IF NOT EXISTS agent_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  topic_id INTEGER REFERENCES research_topics(id) ON DELETE SET NULL,
  chat_id INTEGER REFERENCES chats(id) ON DELETE SET NULL,
  kind TEXT NOT NULL,                -- 'research' | 'chat'
  status TEXT NOT NULL,              -- 'running' | 'ok' | 'failed' | 'capped'
  steps INTEGER NOT NULL DEFAULT 0,
  started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  ended_at TIMESTAMP,
  output TEXT,
  error TEXT
);

CREATE TABLE IF NOT EXISTS usage_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  bucket TEXT NOT NULL,              -- 'llm_step' | 'web_search' | 'fetch' | 'firecrawl' | 'github'
  count INTEGER NOT NULL DEFAULT 1,
  day TEXT NOT NULL,                 -- YYYY-MM-DD UTC
  UNIQUE(user_id, bucket, day)
);

CREATE TABLE IF NOT EXISTS app_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, id);
CREATE INDEX IF NOT EXISTS idx_chats_user ON chats(user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_keys_user ON api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_knowledge_user ON knowledge(user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_topics_user ON research_topics(user_id);
CREATE INDEX IF NOT EXISTS idx_runs_user ON agent_runs(user_id, id DESC);
"""


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as c:
        c.executescript(SCHEMA)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"))


def loads(s: str | None) -> Any:
    return json.loads(s) if s else None
