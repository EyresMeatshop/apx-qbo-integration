from database import get_conn
from config import settings

conn = get_conn()
cur = conn.cursor()

cur.execute("""
    SELECT loyverse_name, qbo_name, qbo_environment, match_method
    FROM item_map
    WHERE qbo_environment = ?
    LIMIT 20
""", (settings.QBO_ENVIRONMENT,))

rows = cur.fetchall()

print(f"Mappings found for environment '{settings.QBO_ENVIRONMENT}': {len(rows)}")
for row in rows:
    print(dict(row))

conn.close()