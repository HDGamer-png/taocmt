"""Persistent PostgreSQL storage with SQLite fallback for local development."""

import json
import os
import shutil
import sqlite3
import threading
from pathlib import Path

import bcrypt

BASE_DIR = Path(__file__).parent
LEGACY_DATA_DIR = BASE_DIR / "data"
LEGACY_HISTORY_DIR = BASE_DIR / "BE" / "history"
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
DATA_DIR = Path(os.environ.get("CMT_DATA_DIR", str(LEGACY_DATA_DIR)))
DB_PATH = Path(os.environ.get("CMT_DB_PATH", str(DATA_DIR / "app.db")))
USING_POSTGRES = bool(DATABASE_URL)

_db_lock = threading.RLock()


def _connect():
    if USING_POSTGRES:
        import psycopg
        from psycopg.rows import dict_row

        url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        return psycopg.connect(url, row_factory=dict_row)

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def _execute(connection, query: str, parameters=()):
    if USING_POSTGRES:
        query = query.replace("?", "%s")
    return connection.execute(query, parameters)


def _read_json(path: Path, default):
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return default


def initialize() -> None:
    """Create tables and import legacy JSON data exactly once."""
    with _db_lock, _connect() as connection:
        schema = """
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                email TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                avatar_path TEXT,
                created_at TEXT NOT NULL,
                last_login TEXT
                ,role TEXT NOT NULL DEFAULT 'user'
                ,is_active BOOLEAN NOT NULL DEFAULT TRUE
            );
            CREATE TABLE IF NOT EXISTS user_documents (
                user_id TEXT NOT NULL,
                document_type TEXT NOT NULL,
                content TEXT NOT NULL,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, document_type),
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS app_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS usage_events (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                requested_count INTEGER NOT NULL DEFAULT 0,
                generated_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                provider TEXT,
                model TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            );
        """
        if USING_POSTGRES:
            for statement in schema.split(";"):
                if statement.strip():
                    _execute(connection, statement)
        else:
            connection.executescript(schema)

        if USING_POSTGRES:
            _execute(connection, "ALTER TABLE users ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'user'")
            _execute(connection, "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE")
        else:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(users)").fetchall()}
            if "role" not in columns:
                connection.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
            if "is_active" not in columns:
                connection.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")

        migrated = _execute(
            connection, "SELECT value FROM app_metadata WHERE key = ?",
            ("legacy_json_migrated",),
        ).fetchone()
        if migrated:
            _ensure_admin(connection)
            return

        users = _read_json(LEGACY_DATA_DIR / "users.json", [])
        for user in users if isinstance(users, list) else []:
            _execute(
                connection,
                """
                INSERT INTO users
                (user_id, username, email, display_name, password_hash,
                 avatar_path, created_at, last_login, role, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                """,
                tuple(user.get(field) for field in (
                    "user_id", "username", "email", "display_name",
                    "password_hash", "avatar_path", "created_at", "last_login",
                )) + (user.get("role", "user"), user.get("is_active", True)),
            )

        legacy_avatars = LEGACY_DATA_DIR / "avatars"
        target_avatars = DATA_DIR / "avatars"
        if legacy_avatars.exists() and legacy_avatars != target_avatars:
            target_avatars.mkdir(parents=True, exist_ok=True)
            for avatar in legacy_avatars.iterdir():
                if avatar.is_file() and not (target_avatars / avatar.name).exists():
                    shutil.copy2(avatar, target_avatars / avatar.name)

        for user_dir in LEGACY_DATA_DIR.iterdir() if LEGACY_DATA_DIR.exists() else []:
            tasks_file = user_dir / "tasks.json"
            if user_dir.is_dir() and tasks_file.exists():
                user_id = user_dir.name
                if _execute(connection, "SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone():
                    content = json.dumps(_read_json(tasks_file, []), ensure_ascii=False)
                    _execute(
                        connection,
                        "INSERT INTO user_documents (user_id, document_type, content) VALUES (?, 'tasks', ?) ON CONFLICT DO NOTHING",
                        (user_id, content),
                    )

        if LEGACY_HISTORY_DIR.exists():
            for history_file in LEGACY_HISTORY_DIR.glob("*.json"):
                user_id = history_file.stem
                if _execute(connection, "SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone():
                    content = json.dumps(_read_json(history_file, {"user_id": user_id, "topics": {}}), ensure_ascii=False)
                    _execute(
                        connection,
                        "INSERT INTO user_documents (user_id, document_type, content) VALUES (?, 'history', ?) ON CONFLICT DO NOTHING",
                        (user_id, content),
                    )

        _execute(
            connection,
            "INSERT INTO app_metadata (key, value) VALUES (?, ?)",
            ("legacy_json_migrated", "1"),
        )
        _ensure_admin(connection)


def _ensure_admin(connection) -> None:
    username = os.environ.get("ADMIN_USERNAME", "").strip()
    email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not username or not email or not password:
        return
    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    query = """
        INSERT INTO users
        (user_id, username, email, display_name, password_hash, created_at, role, is_active)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 'admin', TRUE)
        ON CONFLICT(username) DO UPDATE SET role='admin', is_active=TRUE
    """
    _execute(connection, query, ("admin-" + username[:8], username, email, username, password_hash))


def load_users() -> list[dict]:
    initialize()
    with _db_lock, _connect() as connection:
        rows = _execute(connection, "SELECT * FROM users ORDER BY created_at").fetchall()
        return [dict(row) for row in rows]


def save_users(users: list[dict]) -> None:
    initialize()
    with _db_lock, _connect() as connection:
        for user in users:
            _execute(
                connection,
                """
                INSERT INTO users
                (user_id, username, email, display_name, password_hash,
                 avatar_path, created_at, last_login, role, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username=excluded.username, email=excluded.email,
                    display_name=excluded.display_name, password_hash=excluded.password_hash,
                    avatar_path=excluded.avatar_path, created_at=excluded.created_at,
                    last_login=excluded.last_login
                """,
                tuple(user.get(field) for field in (
                    "user_id", "username", "email", "display_name",
                    "password_hash", "avatar_path", "created_at", "last_login",
                )) + (user.get("role", "user"), user.get("is_active", True)),
            )


def load_document(user_id: str, document_type: str, default):
    initialize()
    with _db_lock, _connect() as connection:
        row = _execute(
            connection,
            "SELECT content FROM user_documents WHERE user_id = ? AND document_type = ?",
            (user_id, document_type),
        ).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["content"])
        except json.JSONDecodeError:
            return default


def save_document(user_id: str, document_type: str, value) -> None:
    initialize()
    content = json.dumps(value, ensure_ascii=False)
    with _db_lock, _connect() as connection:
        _execute(
            connection,
            """
            INSERT INTO user_documents (user_id, document_type, content, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, document_type) DO UPDATE SET
                content=excluded.content, updated_at=CURRENT_TIMESTAMP
            """,
            (user_id, document_type, content),
        )


def update_user_status(user_id: str, is_active: bool) -> bool:
    initialize()
    with _db_lock, _connect() as connection:
        result = _execute(connection, "UPDATE users SET is_active = ? WHERE user_id = ? AND role <> 'admin'", (is_active, user_id))
        return result.rowcount > 0


def list_admin_users() -> list[dict]:
    initialize()
    with _db_lock, _connect() as connection:
        rows = _execute(connection, """
            SELECT u.user_id, u.username, u.email, u.display_name, u.role,
                   u.is_active, u.created_at, u.last_login,
                   COUNT(DISTINCT e.task_id) AS task_count,
                   COALESCE(SUM(e.generated_count), 0) AS generated_count
            FROM users u
            LEFT JOIN usage_events e ON e.user_id = u.user_id
            GROUP BY u.user_id
            ORDER BY u.created_at DESC
        """).fetchall()
        return [dict(row) for row in rows]


def record_usage(user_id: str, task_id: str, requested_count: int,
                 generated_count: int, status: str, provider: str, model: str,
                 created_at: str) -> None:
    initialize()
    with _db_lock, _connect() as connection:
        _execute(connection, """
            INSERT INTO usage_events
            (id, user_id, task_id, requested_count, generated_count, status, provider, model, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (str(__import__("uuid").uuid4()), user_id, task_id, requested_count,
               generated_count, status, provider, model, created_at))


def usage_summary() -> dict:
    initialize()
    with _db_lock, _connect() as connection:
        row = _execute(connection, """
            SELECT COUNT(*) AS event_count,
                   COUNT(DISTINCT user_id) AS active_users,
                   COALESCE(SUM(generated_count), 0) AS generated_count,
                   COALESCE(SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END), 0) AS completed_tasks
            FROM usage_events
        """).fetchone()
        users = _execute(connection, "SELECT COUNT(*) AS total_users FROM users").fetchone()
        return {**dict(row), "total_users": users["total_users"]}


initialize()
