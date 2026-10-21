"""Database connection manager supporting PostgreSQL with SQLite fallback.

Ensures reliable database connectivity with thread-safety and standardized
schema initialization.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Sequence

logger = logging.getLogger(__name__)

_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_DB_LOCK = threading.RLock()


def get_database_url() -> str:
    """Return database connection URL from environment or default SQLite."""
    return os.getenv("DATABASE_URL", "").strip()


def is_postgres() -> bool:
    """Check if the configured database is PostgreSQL."""
    url = get_database_url()
    return url.startswith("postgres://") or url.startswith("postgresql://")


def _get_sqlite_path() -> Path:
    """Return path to local SQLite database file."""
    _DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)
    return _DEFAULT_DATA_DIR / "kinesia.db"


class DatabaseConnection:
    """Wrapper providing unified dictionary row access across PostgreSQL and SQLite."""

    def __init__(self, raw_conn: Any, is_pg: bool) -> None:
        self.raw_conn = raw_conn
        self.is_pg = is_pg

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> Any:
        """Execute query, adapting parameter placeholder (? vs %s) if needed."""
        cursor = self.raw_conn.cursor()
        if self.is_pg:
            # Adapt '?' placeholders to '%s' for PostgreSQL
            adapted_sql = sql.replace("?", "%s")
            cursor.execute(adapted_sql, params or ())
        else:
            cursor.execute(sql, params or ())
        return cursor

    def fetchone(self, sql: str, params: Sequence[Any] | None = None) -> dict[str, Any] | None:
        """Execute query and fetch a single row as a dictionary."""
        cursor = self.execute(sql, params)
        row = cursor.fetchone()
        if row is None:
            return None
        col_names = [desc[0] for desc in cursor.description]
        return dict(zip(col_names, row))

    def fetchall(self, sql: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
        """Execute query and fetch all rows as dictionaries."""
        cursor = self.execute(sql, params)
        rows = cursor.fetchall()
        if not rows:
            return []
        col_names = [desc[0] for desc in cursor.description]
        return [dict(zip(col_names, row)) for row in rows]

    def commit(self) -> None:
        """Commit current transaction."""
        self.raw_conn.commit()

    def rollback(self) -> None:
        """Rollback current transaction."""
        self.raw_conn.rollback()

    def close(self) -> None:
        """Close database connection."""
        self.raw_conn.close()


@contextmanager
def get_db() -> Generator[DatabaseConnection, None, None]:
    """Context manager yielding a DatabaseConnection with auto-commit."""
    url = get_database_url()
    conn_obj: DatabaseConnection

    if is_postgres():
        import psycopg2

        raw_conn = psycopg2.connect(url)
        conn_obj = DatabaseConnection(raw_conn, is_pg=True)
    else:
        db_path = _get_sqlite_path()
        raw_conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn_obj = DatabaseConnection(raw_conn, is_pg=False)

    try:
        yield conn_obj
        conn_obj.commit()
    except Exception:
        conn_obj.rollback()
        raise
    finally:
        conn_obj.close()


def init_db() -> None:
    """Initialize database tables idempotently."""
    with _DB_LOCK:
        with get_db() as db:
            pg = db.is_pg

            bool_type = "BOOLEAN"
            real_type = "DOUBLE PRECISION" if pg else "REAL"

            db.execute(
                f"""
                CREATE TABLE IF NOT EXISTS users (
                    id VARCHAR(36) PRIMARY KEY,
                    username VARCHAR(50) UNIQUE NOT NULL,
                    email VARCHAR(100) UNIQUE NOT NULL,
                    password_hash VARCHAR(255) NOT NULL,
                    salt VARCHAR(64) NOT NULL,
                    display_name VARCHAR(100) NOT NULL,
                    avatar_color VARCHAR(20) DEFAULT '#10B981',
                    default_input_lang VARCHAR(10) DEFAULT 'en',
                    default_output_lang VARCHAR(10) DEFAULT 'hi',
                    gender VARCHAR(10) DEFAULT 'female',
                    created_at {real_type} NOT NULL
                )
                """
            )

            db.execute(
                f"""
                CREATE TABLE IF NOT EXISTS rooms (
                    id VARCHAR(36) PRIMARY KEY,
                    room_code VARCHAR(20) UNIQUE NOT NULL,
                    title VARCHAR(200) NOT NULL,
                    passcode_hash VARCHAR(255),
                    host_id VARCHAR(36) REFERENCES users(id),
                    record_session {bool_type} DEFAULT TRUE,
                    is_active {bool_type} DEFAULT TRUE,
                    created_at {real_type} NOT NULL,
                    ended_at {real_type}
                )
                """
            )

            db.execute(
                f"""
                CREATE TABLE IF NOT EXISTS room_participants (
                    id VARCHAR(36) PRIMARY KEY,
                    room_id VARCHAR(36) REFERENCES rooms(id),
                    user_id VARCHAR(36) REFERENCES users(id),
                    user_name VARCHAR(100) NOT NULL,
                    joined_at {real_type} NOT NULL,
                    left_at {real_type},
                    speaking_lang VARCHAR(10) DEFAULT 'en',
                    listening_lang VARCHAR(10) DEFAULT 'hi',
                    is_mic_muted {bool_type} DEFAULT FALSE
                )
                """
            )

            db.execute(
                f"""
                CREATE TABLE IF NOT EXISTS transcripts (
                    id VARCHAR(36) PRIMARY KEY,
                    room_id VARCHAR(36) REFERENCES rooms(id),
                    speaker_id VARCHAR(36),
                    speaker_name VARCHAR(100) NOT NULL,
                    original_text TEXT NOT NULL,
                    source_lang VARCHAR(10) NOT NULL,
                    confidence {real_type} DEFAULT 1.0,
                    translations_json TEXT NOT NULL,
                    timestamp {real_type} NOT NULL
                )
                """
            )

            db.execute(
                f"""
                CREATE TABLE IF NOT EXISTS session_insights (
                    room_id VARCHAR(36) PRIMARY KEY REFERENCES rooms(id),
                    executive_summary TEXT NOT NULL,
                    action_items_json TEXT NOT NULL,
                    key_points_json TEXT NOT NULL,
                    entities_json TEXT NOT NULL,
                    created_at {real_type} NOT NULL
                )
                """
            )

            # Migration: Ensure gender column exists on existing users table
            try:
                db.execute("ALTER TABLE users ADD COLUMN gender VARCHAR(10) DEFAULT 'female'")
            except Exception:
                pass

            logger.info("Database initialized successfully (engine: %s)", "PostgreSQL" if pg else "SQLite")
