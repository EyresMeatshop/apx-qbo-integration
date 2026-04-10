from config import settings
from database import get_conn, db_backend

print("DB_PATH from settings:", settings.DB_PATH)
print("DB backend:", db_backend())

conn = get_conn()
cur = conn.cursor()

if db_backend() == "sqlite":
    cur.execute("PRAGMA database_list;")
    print("database_list:", cur.fetchall())

if db_backend() == "sqlite":
    cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='item_map';")
    row = cur.fetchone()
    print("item_map schema:", row[0] if row else "item_map table not found")
else:
    cur.execute(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = ?
        ORDER BY ordinal_position
        """,
        ("item_map",),
    )
    cols = cur.fetchall()
    print("item_map columns:", cols if cols else "item_map table not found")

conn.close()