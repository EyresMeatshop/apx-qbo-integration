from database import get_conn

conn = get_conn()
cur = conn.cursor()

cur.execute(
    """
    SELECT loyverse_item_id, loyverse_name, qbo_item_id, qbo_name, usage_count
    FROM item_map_clean_usage
    WHERE qbo_item_id = ?
    """,
    ("21",),
)

rows = cur.fetchall()
print([dict(r) for r in rows])

conn.close()