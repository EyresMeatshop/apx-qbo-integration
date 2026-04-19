from datetime import datetime
from database import get_conn, db_backend


def utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def init_approval_tables():
    conn = get_conn()
    try:
        cur = conn.cursor()

        if db_backend() == "postgres":
            cur.execute("""
                CREATE TABLE IF NOT EXISTS review_users (
                    id SERIAL PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS reconcile_batches (
                    id SERIAL PRIMARY KEY,
                    batch_id TEXT UNIQUE NOT NULL,
                    environment TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    applied_by TEXT,
                    applied_at TEXT
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS reconcile_items (
                    id SERIAL PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    loyverse_item_id TEXT,
                    qbo_item_id TEXT,
                    item_name TEXT NOT NULL,
                    loyverse_qty DOUBLE PRECISION NOT NULL DEFAULT 0,
                    qbo_qty DOUBLE PRECISION NOT NULL DEFAULT 0,
                    difference DOUBLE PRECISION NOT NULL DEFAULT 0,
                    suggested_action TEXT NOT NULL DEFAULT 'LOYVERSE',
                    approved_action TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    saved_by TEXT,
                    saved_at TEXT,
                    applied_by TEXT,
                    applied_at TEXT
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS reconcile_audit_log (
                    id SERIAL PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    reconcile_item_id INTEGER,
                    username TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    old_value TEXT,
                    new_value TEXT,
                    details TEXT,
                    created_at TEXT NOT NULL
                )
            """)
        else:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS review_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS reconcile_batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_id TEXT UNIQUE NOT NULL,
                    environment TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    applied_by TEXT,
                    applied_at TEXT
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS reconcile_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_id TEXT NOT NULL,
                    loyverse_item_id TEXT,
                    qbo_item_id TEXT,
                    item_name TEXT NOT NULL,
                    loyverse_qty REAL NOT NULL DEFAULT 0,
                    qbo_qty REAL NOT NULL DEFAULT 0,
                    difference REAL NOT NULL DEFAULT 0,
                    suggested_action TEXT NOT NULL DEFAULT 'LOYVERSE',
                    approved_action TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    saved_by TEXT,
                    saved_at TEXT,
                    applied_by TEXT,
                    applied_at TEXT
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS reconcile_audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_id TEXT NOT NULL,
                    reconcile_item_id INTEGER,
                    username TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    old_value TEXT,
                    new_value TEXT,
                    details TEXT,
                    created_at TEXT NOT NULL
                )
            """)

        conn.commit()
    finally:
        conn.close()


def get_user_by_username(username: str):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT *
            FROM review_users
            WHERE username = ? AND is_active = 1
            LIMIT 1
        """, (username,))
        return cur.fetchone()
    finally:
        conn.close()


def create_user(username: str, password_hash: str):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO review_users (username, password_hash, is_active, created_at)
            VALUES (?, ?, 1, ?)
        """, (username, password_hash, utc_now()))
        conn.commit()
    finally:
        conn.close()


def update_user_password(username: str, password_hash: str) -> bool:
    """Set a new password hash for an active review user. Returns True if a row was updated."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE review_users
            SET password_hash = ?
            WHERE username = ? AND is_active = 1
        """, (password_hash, username))
        conn.commit()
        rc = getattr(cur, "rowcount", None)
        if rc is not None:
            return int(rc) > 0
        return True
    finally:
        conn.close()


def create_batch(batch_id: str, environment: str):
    conn = get_conn()
    try:
        cur = conn.cursor()
        if db_backend() == "postgres":
            cur.execute("""
                INSERT INTO reconcile_batches (batch_id, environment, status, created_at)
                VALUES (?, ?, 'pending', ?)
                ON CONFLICT (batch_id) DO NOTHING
            """, (batch_id, environment, utc_now()))
        else:
            cur.execute("""
                INSERT OR IGNORE INTO reconcile_batches (batch_id, environment, status, created_at)
                VALUES (?, ?, 'pending', ?)
            """, (batch_id, environment, utc_now()))
        conn.commit()
    finally:
        conn.close()


def get_batch(batch_id: str):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT *
            FROM reconcile_batches
            WHERE batch_id = ?
            LIMIT 1
        """, (batch_id,))
        return cur.fetchone()
    finally:
        conn.close()


def get_batch_items(batch_id: str):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT *
            FROM reconcile_items
            WHERE batch_id = ?
            ORDER BY item_name
        """, (batch_id,))
        return cur.fetchall()
    finally:
        conn.close()


def get_reconcile_item(batch_id: str, item_id: int):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT *
            FROM reconcile_items
            WHERE batch_id = ? AND id = ?
            LIMIT 1
        """, (batch_id, item_id))
        return cur.fetchone()
    finally:
        conn.close()


def finalize_batch_if_all_items_done(batch_id: str, username: str):
    """When every reconcile line is no longer pending, mark the batch as applied."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) AS c FROM reconcile_items WHERE batch_id = ?",
            (batch_id,),
        )
        total = int(cur.fetchone()["c"])
        if total == 0:
            return
        cur.execute(
            """
            SELECT COUNT(*) AS c FROM reconcile_items
            WHERE batch_id = ? AND status = 'pending'
            """,
            (batch_id,),
        )
        pending = int(cur.fetchone()["c"])
        if pending > 0:
            return
        cur.execute(
            """
            UPDATE reconcile_batches
            SET status = 'applied', applied_by = ?, applied_at = ?
            WHERE batch_id = ?
            """,
            (username, utc_now(), batch_id),
        )
        conn.commit()
    finally:
        conn.close()


def complete_reconcile_item(
    batch_id: str,
    item_id: int,
    approved_action: str,
    username: str,
    outcome_status: str,
):
    """
    Record final decision and mark row complete.
    outcome_status: 'applied' (stock was updated or ignore recorded) or 'ignored'.
    """
    now = utc_now()
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE reconcile_items
            SET approved_action = ?,
                updated_at = ?,
                saved_by = ?,
                saved_at = ?,
                status = ?,
                applied_by = ?,
                applied_at = ?
            WHERE id = ? AND batch_id = ?
        """, (
            approved_action,
            now,
            username,
            now,
            outcome_status,
            username,
            now,
            item_id,
            batch_id,
        ))
        conn.commit()
    finally:
        conn.close()


def add_reconcile_item(
    batch_id: str,
    loyverse_item_id: str,
    qbo_item_id: str,
    item_name: str,
    loyverse_qty: float,
    qbo_qty: float,
    difference: float,
    suggested_action: str,
):
    now = utc_now()
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO reconcile_items (
                batch_id, loyverse_item_id, qbo_item_id, item_name,
                loyverse_qty, qbo_qty, difference, suggested_action,
                approved_action, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', 'pending', ?, ?)
        """, (
            batch_id,
            loyverse_item_id,
            qbo_item_id,
            item_name,
            loyverse_qty,
            qbo_qty,
            difference,
            suggested_action,
            now,
            now,
        ))
        conn.commit()
    finally:
        conn.close()


def log_audit(
    batch_id: str,
    username: str,
    action_type: str,
    reconcile_item_id: int | None = None,
    old_value: str | None = None,
    new_value: str | None = None,
    details: str | None = None,
):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO reconcile_audit_log (
                batch_id, reconcile_item_id, username, action_type,
                old_value, new_value, details, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            batch_id,
            reconcile_item_id,
            username,
            action_type,
            old_value,
            new_value,
            details,
            utc_now(),
        ))
        conn.commit()
    finally:
        conn.close()


def save_batch_decisions(batch_id: str, decisions: dict[int, str], username: str):
    now = utc_now()
    conn = get_conn()
    try:
        cur = conn.cursor()

        for row_id, approved_action in decisions.items():
            cur.execute("""
                SELECT approved_action
                FROM reconcile_items
                WHERE id = ? AND batch_id = ?
                LIMIT 1
            """, (row_id, batch_id))
            existing = cur.fetchone()
            old_value = existing["approved_action"] if existing else ""

            cur.execute("""
                UPDATE reconcile_items
                SET approved_action = ?,
                    updated_at = ?,
                    saved_by = ?,
                    saved_at = ?
                WHERE id = ? AND batch_id = ?
            """, (approved_action, now, username, now, row_id, batch_id))

            if old_value != approved_action:
                log_audit(
                    batch_id=batch_id,
                    reconcile_item_id=row_id,
                    username=username,
                    action_type="save_decision",
                    old_value=old_value,
                    new_value=approved_action,
                    details="Reviewer updated approved action",
                )

        conn.commit()
    finally:
        conn.close()


def mark_batch_status(batch_id: str, status: str, username: str | None = None):
    conn = get_conn()
    try:
        cur = conn.cursor()
        if status == "applied" and username:
            cur.execute("""
                UPDATE reconcile_batches
                SET status = ?, applied_by = ?, applied_at = ?
                WHERE batch_id = ?
            """, (status, username, utc_now(), batch_id))
        else:
            cur.execute("""
                UPDATE reconcile_batches
                SET status = ?
                WHERE batch_id = ?
            """, (status, batch_id))
        conn.commit()
    finally:
        conn.close()


def mark_item_applied(item_id: int, username: str, status: str = "applied"):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE reconcile_items
            SET status = ?, updated_at = ?, applied_by = ?, applied_at = ?
            WHERE id = ?
        """, (status, utc_now(), username, utc_now(), item_id))
        conn.commit()
    finally:
        conn.close()


def get_approved_items(batch_id: str):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT *
            FROM reconcile_items
            WHERE batch_id = ?
              AND approved_action IN ('QBO', 'LOYVERSE', 'IGNORE')
            ORDER BY item_name
        """, (batch_id,))
        return cur.fetchall()
    finally:
        conn.close()


def get_audit_log(batch_id: str):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT *
            FROM reconcile_audit_log
            WHERE batch_id = ?
            ORDER BY created_at DESC, id DESC
        """, (batch_id,))
        return cur.fetchall()
    finally:
        conn.close()