from database import get_conn

conn = get_conn()
cur = conn.cursor()

cur.execute(
    """
    SELECT id, source_system, event_type, source_reference, item_id, item_name,
           quantity, status, error_message
    FROM sync_events
    WHERE source_reference = ?
    ORDER BY id
    """,
    ("1022",),
)

rows = cur.fetchall()
print([dict(r) for r in rows])

conn.close()