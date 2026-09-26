import os
import re
import sqlite3
from contextlib import contextmanager

from streamlit.errors import StreamlitSecretNotFoundError

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rc_mastery.db")


def _get_database_url() -> str | None:
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        return database_url

    try:
        import streamlit as st

        database_url = st.secrets.get("DATABASE_URL")
        app_environment = os.environ.get("APP_ENV") or st.secrets.get("APP_ENV")
    except StreamlitSecretNotFoundError:
        database_url = None
        app_environment = os.environ.get("APP_ENV")
    if not database_url and app_environment == "production":
        raise RuntimeError("DATABASE_URL is required when APP_ENV is set to production.")
    return database_url


def _postgres_query(query: str) -> str:
    query = query.replace("?", "%s")
    query = re.sub(
        r"\bINTEGER\s+PRIMARY KEY\s+AUTOINCREMENT\b",
        "BIGSERIAL PRIMARY KEY",
        query,
        flags=re.IGNORECASE,
    )
    query = re.sub(r"\bINTEGER\b", "BIGINT", query, flags=re.IGNORECASE)
    if re.match(r"\s*INSERT\s+OR\s+IGNORE\s+INTO\b", query, flags=re.IGNORECASE):
        query = re.sub(
            r"\bINSERT\s+OR\s+IGNORE\s+INTO\b",
            "INSERT INTO",
            query,
            count=1,
            flags=re.IGNORECASE,
        )
        query = query.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    return query


class _PostgresCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, query: str, params=()):
        self._cursor.execute(_postgres_query(query), params)
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    @property
    def lastrowid(self):
        raise RuntimeError("Use insert_and_get_id() for PostgreSQL inserts.")


class _PostgresConnection:
    is_postgres = True

    def __init__(self, connection):
        self._connection = connection

    def execute(self, query: str, params=()):
        return _PostgresCursor(self._connection.cursor()).execute(query, params)

    def cursor(self):
        return _PostgresCursor(self._connection.cursor())

    def commit(self):
        self._connection.commit()

    def close(self):
        self._connection.close()


def insert_and_get_id(conn, query: str, params=()) -> int:
    """Inserts a row and returns its generated ID for SQLite or PostgreSQL."""
    cursor = conn.cursor()
    if getattr(conn, "is_postgres", False):
        cursor.execute(f"{query.rstrip().rstrip(';')} RETURNING id", params)
        return cursor.fetchone()["id"]
    cursor.execute(query, params)
    return cursor.lastrowid


def is_unique_violation(error: Exception) -> bool:
    """Recognizes duplicate-key errors from either supported database."""
    return isinstance(error, sqlite3.IntegrityError) or getattr(error, "sqlstate", None) == "23505"


@contextmanager
def get_db_connection():
    """Provides a connection using hosted PostgreSQL when configured, otherwise local SQLite."""
    database_url = _get_database_url()
    if database_url:
        import psycopg
        from psycopg.rows import dict_row

        normalized_url = database_url.replace("postgres://", "postgresql://", 1)
        conn = _PostgresConnection(psycopg.connect(normalized_url, row_factory=dict_row))
    else:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def initialize_database():
    """Generates the complete relational schema for the RC Mastery platform."""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT DEFAULT 'student',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS benchmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exam_name TEXT UNIQUE NOT NULL,
                target_accuracy REAL NOT NULL,
                target_time_per_question INTEGER NOT NULL
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS system_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                setting_key TEXT UNIQUE NOT NULL,
                setting_value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS passages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                custom_id TEXT UNIQUE,
                title TEXT,
                content TEXT NOT NULL,
                topic TEXT,
                difficulty TEXT,
                source TEXT,
                is_published INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                passage_id INTEGER NOT NULL,
                question_text TEXT NOT NULL,
                question_type TEXT NOT NULL,
                suggested_time INTEGER DEFAULT 90,
                FOREIGN KEY (passage_id) REFERENCES passages (id) ON DELETE CASCADE
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS options (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question_id INTEGER NOT NULL,
                label TEXT NOT NULL,
                option_text TEXT NOT NULL,
                is_correct INTEGER DEFAULT 0,
                explanation TEXT,
                trap_explanation TEXT,
                supporting_lines TEXT,
                FOREIGN KEY (question_id) REFERENCES questions (id) ON DELETE CASCADE
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                passage_id INTEGER NOT NULL,
                total_score REAL DEFAULT 0,
                reading_time_seconds INTEGER DEFAULT 0,
                total_time_seconds INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
                FOREIGN KEY (passage_id) REFERENCES passages (id) ON DELETE CASCADE
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS question_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                attempt_id INTEGER NOT NULL,
                question_id INTEGER NOT NULL,
                selected_option_id INTEGER,
                selected_text TEXT DEFAULT '',
                time_taken_seconds INTEGER DEFAULT 0,
                is_correct INTEGER DEFAULT 0,
                FOREIGN KEY (attempt_id) REFERENCES attempts (id) ON DELETE CASCADE,
                FOREIGN KEY (question_id) REFERENCES questions (id) ON DELETE CASCADE,
                FOREIGN KEY (selected_option_id) REFERENCES options (id) ON DELETE SET NULL
            )
            """
        )

        if not getattr(conn, "is_postgres", False):
            table_info = cursor.execute("PRAGMA table_info(question_attempts)").fetchall()
            columns = {row[1] for row in table_info}
            if "selected_text" not in columns:
                cursor.execute("ALTER TABLE question_attempts ADD COLUMN selected_text TEXT DEFAULT ''")

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS bookmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                passage_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
                FOREIGN KEY (passage_id) REFERENCES passages (id) ON DELETE CASCADE,
                UNIQUE(user_id, passage_id)
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                passage_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
                FOREIGN KEY (passage_id) REFERENCES passages (id) ON DELETE CASCADE
            )
            """
        )

        default_benchmarks = [
            ("IPMAT Indore", 78.0, 65),
            ("IPMAT Rohtak", 75.0, 68),
            ("JIPMAT", 72.0, 70),
            ("NPAT", 70.0, 72),
            ("XAT", 80.0, 60),
            ("CAT", 82.0, 58),
        ]
        for exam_name, target_accuracy, target_time in default_benchmarks:
            cursor.execute(
                "INSERT OR IGNORE INTO benchmarks (exam_name, target_accuracy, target_time_per_question) VALUES (?, ?, ?)",
                (exam_name, target_accuracy, target_time),
            )

        default_settings = {
            "app_title": "RC Mastery Platform",
            "practice_mode": "exam_review",
            "analysis_depth": "detailed",
            "show_answers_after_submission": "true",
        }
        for key, value in default_settings.items():
            cursor.execute(
                "INSERT OR IGNORE INTO system_settings (setting_key, setting_value) VALUES (?, ?)",
                (key, value),
            )

        conn.commit()
        print("Database initialized successfully. All tables created.")


if __name__ == "__main__":
    initialize_database()