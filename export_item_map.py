import csv

OUTPUT_FILE = "item_map_export.csv"

from database import get_conn

conn = get_conn()
cur = conn.cursor()

cur.execute("""
    SELECT
        loyverse_item_id,
        loyverse_name,
        qbo_item_id,
        qbo_name
    FROM item_map
    ORDER BY loyverse_name
""")

rows = cur.fetchall()

with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow([
        "loyverse_item_id",
        "loyverse_name",
        "qbo_item_id",
        "qbo_name"
    ])

    for r in rows:
        writer.writerow([
            r["loyverse_item_id"],
            r["loyverse_name"],
            r["qbo_item_id"],
            r["qbo_name"],
        ])

conn.close()

print(f"Export complete → {OUTPUT_FILE}")