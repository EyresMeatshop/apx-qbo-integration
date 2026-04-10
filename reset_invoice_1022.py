from database import get_conn

conn = get_conn()
cur = conn.cursor()

cur.execute(
    """
    UPDATE sync_events
    SET status = ?, error_message = NULL, processed_at = NULL
    WHERE source_reference = ? AND event_type = ?
    """,
    ("pending", "1022", "invoice_item"),
)

conn.commit()
print(f"Rows updated: {cur.rowcount}")
conn.close()
print("invoice 1022 item events reset")