import os
import sqlite3
from config import settings


def get_conn():
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_column(cur, table_name, column_name, column_def):
    cur.execute(f"PRAGMA table_info({table_name})")
    cols = [row[1] for row in cur.fetchall()]
    if column_name not in cols:
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS item_map (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
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

    cur.execute("""
    CREATE TABLE IF NOT EXISTS receipt_map (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
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

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("Database initialized.")