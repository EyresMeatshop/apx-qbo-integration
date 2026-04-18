from database import get_conn
from config import settings

conn = get_conn()
cur = conn.cursor()

cur.execute(
    """
    SELECT COUNT(*) AS c
    FROM item_map
    WHERE qbo_environment = ?
    """,
    (settings.QBO_ENVIRONMENT,),
)
total = int(cur.fetchone()["c"])

cur.execute(
    """
    SELECT loyverse_name, qbo_name, qbo_environment, match_method
    FROM item_map
    WHERE qbo_environment = ?
    ORDER BY loyverse_name
    LIMIT 30
    """,
    (settings.QBO_ENVIRONMENT,),
)

rows = cur.fetchall()

print(f"Total item_map rows for environment '{settings.QBO_ENVIRONMENT}': {total}")
print(f"Sample (up to 30):")
for row in rows:
    print(dict(row))

if total == 0:
    print(
        "\nNo mappings — nightly reconciliation will show no discrepancies. "
        "Run: python sync_items_loyverse_to_qbo.py"
    )

conn.close()