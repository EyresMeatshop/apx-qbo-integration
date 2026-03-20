from config import settings
from database import get_conn

print("DB_PATH from settings:", settings.DB_PATH)

conn = get_conn()
cur = conn.cursor()

cur.execute("PRAGMA database_list;")
print("database_list:", cur.fetchall())

cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='item_map';")
row = cur.fetchone()
print("item_map schema:", row[0] if row else "item_map table not found")

conn.close()