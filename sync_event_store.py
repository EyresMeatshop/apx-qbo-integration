from datetime import datetime
from database import get_conn, db_backend


def utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _get_table_columns(cur, table_name: str) -> set[str]:
    if db_backend() == "postgres":
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = ?
            """,
            (table_name,),
        )
        rows = cur.fetchall()
        return {row["column_name"] for row in rows} if rows else set()

    cur.execute(f"PRAGMA table_info({table_name})")
    rows = cur.fetchall()
    return {row["name"] for row in rows} if rows else set()


def init_sync_event_tables():
    conn = get_conn()
    try:
        cur = conn.cursor()

        if db_backend() == "postgres":
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sync_events (
                    id SERIAL PRIMARY KEY,
                    source_system TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    source_event_id TEXT NOT NULL,
                    source_reference TEXT,
                    item_id TEXT,
                    item_name TEXT,
                    quantity DOUBLE PRECISION,
                    raw_payload TEXT,
                    event_hash TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    processed_at TEXT,
                    UNIQUE(source_system, source_event_id, event_type)
                )
            """)
        else:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sync_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_system TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    source_event_id TEXT NOT NULL,
                    source_reference TEXT,
                    item_id TEXT,
                    item_name TEXT,
                    quantity REAL,
                    raw_payload TEXT,
                    event_hash TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    processed_at TEXT,
                    UNIQUE(source_system, source_event_id, event_type)
                )
            """)

        if db_backend() == "postgres":
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sync_links (
                    id SERIAL PRIMARY KEY,
                    source_system TEXT NOT NULL,
                    source_event_id TEXT NOT NULL,
                    target_system TEXT NOT NULL,
                    target_reference TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
        else:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sync_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_system TEXT NOT NULL,
                    source_event_id TEXT NOT NULL,
                    target_system TEXT NOT NULL,
                    target_reference TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS sync_state (
                state_key TEXT PRIMARY KEY,
                state_value TEXT,
                updated_at TEXT NOT NULL
            )
        """)

        # Repair older/bad sync_state schema if needed
        sync_state_cols = _get_table_columns(cur, "sync_state")
        if "state_value" not in sync_state_cols:
            cur.execute("DROP TABLE IF EXISTS sync_state")
            cur.execute("""
                CREATE TABLE sync_state (
                    state_key TEXT PRIMARY KEY,
                    state_value TEXT,
                    updated_at TEXT NOT NULL
                )
            """)

        conn.commit()
    finally:
        conn.close()


def event_exists(source_system: str, event_type: str, source_event_id: str) -> bool:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT 1
            FROM sync_events
            WHERE source_system = ?
              AND event_type = ?
              AND source_event_id = ?
            LIMIT 1
        """, (source_system, event_type, source_event_id))
        return cur.fetchone() is not None
    finally:
        conn.close()


def create_event(
    source_system: str,
    event_type: str,
    source_event_id: str,
    source_reference: str = "",
    item_id: str = "",
    item_name: str = "",
    quantity: float | None = None,
       raw_payload: str = "",
    event_hash: str = "",
):
    conn = get_conn()
    try:
        cur = conn.cursor()
        if db_backend() == "postgres":
            cur.execute("""
                INSERT INTO sync_events (
                    source_system, event_type, source_event_id, source_reference,
                    item_id, item_name, quantity, raw_payload, event_hash,
                    status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                ON CONFLICT (source_system, source_event_id, event_type) DO NOTHING
            """, (
                source_system,
                event_type,
                source_event_id,
                source_reference,
                item_id,
                item_name,
                quantity,
                raw_payload,
                event_hash,
                utc_now(),
            ))
        else:
            cur.execute("""
                INSERT OR IGNORE INTO sync_events (
                    source_system, event_type, source_event_id, source_reference,
                    item_id, item_name, quantity, raw_payload, event_hash,
                    status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """, (
                source_system,
                event_type,
                source_event_id,
                source_reference,
                item_id,
                item_name,
                quantity,
                raw_payload,
                event_hash,
                utc_now(),
            ))
        conn.commit()
    finally:
        conn.close()


def get_pending_events(limit: int = 100):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT *
            FROM sync_events
            WHERE status = 'pending'
            ORDER BY created_at ASC, id ASC
            LIMIT ?
        """, (limit,))
        return cur.fetchall()
    finally:
        conn.close()


def mark_event_processing(event_id: int):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE sync_events
            SET status = 'processing'
            WHERE id = ?
        """, (event_id,))
        conn.commit()
    finally:
        conn.close()


def mark_event_processed(event_id: int):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE sync_events
            SET status = 'processed',
                processed_at = ?
            WHERE id = ?
        """, (utc_now(), event_id))
        conn.commit()
    finally:
        conn.close()


def mark_event_ignored(event_id: int, reason: str = ""):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE sync_events
            SET status = 'ignored',
                error_message = ?,
                processed_at = ?
            WHERE id = ?
        """, (reason, utc_now(), event_id))
        conn.commit()
    finally:
        conn.close()


def mark_event_failed(event_id: int, error_message: str):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE sync_events
            SET status = 'failed',
                error_message = ?,
                processed_at = ?
            WHERE id = ?
        """, (str(error_message)[:2000], utc_now(), event_id))
        conn.commit()
    finally:
        conn.close()


def create_sync_link(
    source_system: str,
    source_event_id: str,
    target_system: str,
    target_reference: str,
):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO sync_links (
                source_system, source_event_id, target_system, target_reference, created_at
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            source_system,
            source_event_id,
            target_system,
            target_reference,
            utc_now(),
        ))
        conn.commit()
    finally:
        conn.close()


def get_sync_link(source_system: str, source_event_id: str, target_system: str):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT *
            FROM sync_links
            WHERE source_system = ?
              AND source_event_id = ?
              AND target_system = ?
            ORDER BY id DESC
            LIMIT 1
        """, (source_system, source_event_id, target_system))
        return cur.fetchone()
    finally:
        conn.close()


def get_state(state_key: str, default_value: str = "") -> str:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT state_value
            FROM sync_state
            WHERE state_key = ?
            LIMIT 1
        """, (state_key,))
        row = cur.fetchone()
        return row["state_value"] if row else default_value
    finally:
        conn.close()


def set_state(state_key: str, state_value: str):
    conn = get_conn()
    try:
        cur = conn.cursor()
        if db_backend() == "postgres":
            cur.execute("""
                INSERT INTO sync_state (state_key, state_value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(state_key)
                DO UPDATE SET
                    state_value = EXCLUDED.state_value,
                    updated_at = EXCLUDED.updated_at
            """, (state_key, state_value, utc_now()))
        else:
            cur.execute("""
                INSERT INTO sync_state (state_key, state_value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(state_key)
                DO UPDATE SET
                    state_value = excluded.state_value,
                    updated_at = excluded.updated_at
            """, (state_key, state_value, utc_now()))
        conn.commit()
    finally:
        conn.close()