import os
import sqlite3
from typing import Any

from config import settings

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover
    psycopg = None
    dict_row = None


def _backend() -> str:
    return "postgres" if settings.DATABASE_URL else "sqlite"


def db_backend() -> str:
    return _backend()


class _PgCursor:
    def __init__(self, inner):
        self._inner = inner

    def execute(self, sql: str, params: tuple | list | None = None):
        sql_pg = sql.replace("?", "%s")
        return self._inner.execute(sql_pg, params or ())

    def executemany(self, sql: str, seq_of_params):
        sql_pg = sql.replace("?", "%s")
        return self._inner.executemany(sql_pg, seq_of_params)

    def fetchall(self):
        return self._inner.fetchall()

    def fetchone(self):
        return self._inner.fetchone()

    def __getattr__(self, item: str):
        return getattr(self._inner, item)


class _PgConnection:
    def __init__(self, inner):
        self._inner = inner

    def cursor(self, *args, **kwargs):
        # Force dict rows to mimic sqlite3.Row indexing by column name
        kwargs.setdefault("row_factory", dict_row)
        return _PgCursor(self._inner.cursor(*args, **kwargs))

    def commit(self):
        return self._inner.commit()

    def rollback(self):
        return self._inner.rollback()

    def close(self):
        return self._inner.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


def get_conn():
    if _backend() == "postgres":
        if psycopg is None:
            raise RuntimeError("DATABASE_URL is set but psycopg is not installed.")
        # Use autocommit for simple CRUD helpers. On Postgres, any SQL error inside a
        # transaction marks it aborted until ROLLBACK; autocommit avoids that footgun
        # across unrelated sequential queries that share no transactional intent.
        conn = psycopg.connect(settings.DATABASE_URL, autocommit=True)
        return _PgConnection(conn)

    db_dir = os.path.dirname(settings.DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_column(cur, table_name: str, column_name: str, column_def: str):
    if _backend() == "postgres":
        cur.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = ? AND column_name = ?
            LIMIT 1
            """,
            (table_name, column_name),
        )
        if cur.fetchone() is None:
            cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")
        return

    cur.execute(f"PRAGMA table_info({table_name})")
    cols = [row[1] for row in cur.fetchall()]
    if column_name not in cols:
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")


def _pk_autoinc_sql() -> str:
    return "SERIAL PRIMARY KEY" if _backend() == "postgres" else "INTEGER PRIMARY KEY AUTOINCREMENT"


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    pk = _pk_autoinc_sql()

    cur.execute(f"""
    CREATE TABLE IF NOT EXISTS item_map (
        id {pk},
        loyverse_item_id TEXT,
        loyverse_variant_id TEXT,
        qbo_item_id TEXT,
        sku TEXT,
        loyverse_name TEXT,
        qbo_name TEXT,
        qbo_environment TEXT,
        match_method TEXT,
        last_source TEXT,
        last_synced_at TEXT
    )
    """)

    ensure_column(cur, "item_map", "qbo_environment", "TEXT")
    ensure_column(cur, "item_map", "match_method", "TEXT")
    ensure_column(cur, "item_map", "last_source", "TEXT")
    ensure_column(cur, "item_map", "last_synced_at", "TEXT")

    cur.execute(f"""
    CREATE TABLE IF NOT EXISTS receipt_map (
        id {pk},
        loyverse_receipt_id TEXT,
        qbo_sales_receipt_id TEXT,
        qbo_environment TEXT,
        loyverse_receipt_number TEXT,
        total_amount TEXT,
        synced_at TEXT
    )
    """)

    ensure_column(cur, "receipt_map", "qbo_environment", "TEXT")
    ensure_column(cur, "receipt_map", "loyverse_receipt_number", "TEXT")
    ensure_column(cur, "receipt_map", "total_amount", "TEXT")
    ensure_column(cur, "receipt_map", "synced_at", "TEXT")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sync_state (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)

    cur.execute(f"""
    CREATE TABLE IF NOT EXISTS qbo_oauth_tokens (
        id {pk},
        realm_id TEXT NOT NULL UNIQUE,
        access_token TEXT NOT NULL,
        refresh_token TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """)

    conn.commit()
    conn.close()


def upsert_qbo_tokens(realm_id: str, access_token: str, refresh_token: str, updated_at: str):
    if not realm_id:
        return
    conn = get_conn()
    try:
        cur = conn.cursor()
        if _backend() == "postgres":
            cur.execute(
                """
                INSERT INTO qbo_oauth_tokens (realm_id, access_token, refresh_token, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (realm_id)
                DO UPDATE SET
                    access_token = EXCLUDED.access_token,
                    refresh_token = EXCLUDED.refresh_token,
                    updated_at = EXCLUDED.updated_at
                """,
                (realm_id, access_token, refresh_token, updated_at),
            )
        else:
            cur.execute(
                """
                INSERT INTO qbo_oauth_tokens (realm_id, access_token, refresh_token, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(realm_id)
                DO UPDATE SET
                    access_token = excluded.access_token,
                    refresh_token = excluded.refresh_token,
                    updated_at = excluded.updated_at
                """,
                (realm_id, access_token, refresh_token, updated_at),
            )
            conn.commit()
    except Exception:
        # Ensure we never leave a Postgres connection stuck in an aborted transaction.
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def load_qbo_tokens(realm_id: str) -> dict[str, str] | None:
    if not realm_id:
        return None
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT access_token, refresh_token
            FROM qbo_oauth_tokens
            WHERE realm_id = ?
            LIMIT 1
            """,
            (realm_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "access_token": row["access_token"],
            "refresh_token": row["refresh_token"],
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    print("Database initialized.")
