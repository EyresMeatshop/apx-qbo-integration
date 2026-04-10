import csv
from collections import defaultdict, Counter
from pathlib import Path

OUT_DIR = Path("mapping_audit")
OUT_DIR.mkdir(exist_ok=True)

from database import get_conn, db_backend


def export_csv(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def fetch_item_map(conn):
    cur = conn.cursor()
    cur.execute("""
        SELECT
            id as map_rowid,
            loyverse_item_id,
            loyverse_name,
            qbo_item_id,
            qbo_name
        FROM item_map
        ORDER BY loyverse_name, qbo_item_id
    """)
    return cur.fetchall()


def collect_qbo_usage_from_sync_events(conn):
    """
    Reads qbo item usage from already-created invoice_item / sale_receipt_item events.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT source_event_id
        FROM sync_events
        WHERE source_system = 'QBO'
          AND event_type IN ('invoice_item', 'sale_receipt_item')
    """)
    rows = cur.fetchall()

    usage = Counter()

    for r in rows:
        source_event_id = r["source_event_id"] or ""
        # expected shapes:
        # invoice_id:line_num:qbo_item_id
        # receipt_id:line_num:qbo_item_id
        parts = source_event_id.split(":")
        if len(parts) >= 3:
            qbo_item_id = parts[-1].strip()
            if qbo_item_id:
                usage[qbo_item_id] += 1

    return usage


def choose_canonical_mapping_by_usage(rows, usage_counter):
    by_loyverse = defaultdict(list)
    for r in rows:
        key = (r["loyverse_item_id"], r["loyverse_name"])
        by_loyverse[key].append(r)

    cleaned = []
    review = []

    for (loy_id, loy_name), items in sorted(by_loyverse.items(), key=lambda x: (x[0][1] or "", x[0][0] or "")):
        if len(items) == 1:
            r = items[0]
            cleaned.append({
                "loyverse_item_id": r["loyverse_item_id"],
                "loyverse_name": r["loyverse_name"],
                "qbo_item_id": r["qbo_item_id"],
                "qbo_name": r["qbo_name"],
                "usage_count": usage_counter.get(str(r["qbo_item_id"]), 0),
                "selection_reason": "only_mapping",
            })
            continue

        ranked = []
        for r in items:
            qbo_id = str(r["qbo_item_id"])
            usage = usage_counter.get(qbo_id, 0)

            # ranking:
            # 1. highest usage count
            # 2. lowest numeric qbo id as tie-break
            if qbo_id.isdigit():
                numeric_id = int(qbo_id)
            else:
                numeric_id = 999999999

            ranked.append((usage, -numeric_id, r))

        ranked.sort(reverse=True)
        chosen = ranked[0][2]
        chosen_usage = usage_counter.get(str(chosen["qbo_item_id"]), 0)

        if chosen_usage > 0:
            reason = "highest_transaction_usage"
        else:
            reason = "no_usage_found_fallback_lowest_id"

            # if no usage at all, choose lowest numeric id
            def sort_key(row):
                q = str(row["qbo_item_id"])
                return (0, int(q)) if q.isdigit() else (1, q)

            chosen = sorted(items, key=sort_key)[0]
            chosen_usage = usage_counter.get(str(chosen["qbo_item_id"]), 0)

        cleaned.append({
            "loyverse_item_id": chosen["loyverse_item_id"],
            "loyverse_name": chosen["loyverse_name"],
            "qbo_item_id": chosen["qbo_item_id"],
            "qbo_name": chosen["qbo_name"],
            "usage_count": chosen_usage,
            "selection_reason": reason,
        })

        for r in items:
            qbo_id = str(r["qbo_item_id"])
            usage = usage_counter.get(qbo_id, 0)
            review.append({
                "loyverse_item_id": r["loyverse_item_id"],
                "loyverse_name": r["loyverse_name"],
                "qbo_item_id": r["qbo_item_id"],
                "qbo_name": r["qbo_name"],
                "usage_count": usage,
                "keep": "YES" if str(r["qbo_item_id"]) == str(chosen["qbo_item_id"]) else "NO",
            })

    return cleaned, review


def create_clean_usage_table(conn):
    cur = conn.cursor()
    if db_backend() == "postgres":
        cur.execute("""
            CREATE TABLE IF NOT EXISTS item_map_clean_usage (
                id SERIAL PRIMARY KEY,
                loyverse_item_id TEXT NOT NULL,
                loyverse_name TEXT,
                qbo_item_id TEXT NOT NULL,
                qbo_name TEXT,
                usage_count INTEGER DEFAULT 0,
                selection_reason TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
    else:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS item_map_clean_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                loyverse_item_id TEXT NOT NULL,
                loyverse_name TEXT,
                qbo_item_id TEXT NOT NULL,
                qbo_name TEXT,
                usage_count INTEGER DEFAULT 0,
                selection_reason TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
    cur.execute("DELETE FROM item_map_clean_usage")
    conn.commit()


def load_clean_usage_table(conn, rows):
    cur = conn.cursor()
    cur.executemany("""
        INSERT INTO item_map_clean_usage (
            loyverse_item_id, loyverse_name, qbo_item_id, qbo_name, usage_count, selection_reason
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, [
        (
            r["loyverse_item_id"],
            r["loyverse_name"],
            r["qbo_item_id"],
            r["qbo_name"],
            r["usage_count"],
            r["selection_reason"],
        )
        for r in rows
    ])
    conn.commit()


def main():
    conn = get_conn()
    try:
        rows = fetch_item_map(conn)
        usage_counter = collect_qbo_usage_from_sync_events(conn)

        print(f"Total item_map rows: {len(rows)}")
        print(f"QBO item ids with observed transaction usage: {len(usage_counter)}")

        cleaned, review = choose_canonical_mapping_by_usage(rows, usage_counter)

        export_csv(
            OUT_DIR / "item_map_clean_usage_preview.csv",
            ["loyverse_item_id", "loyverse_name", "qbo_item_id", "qbo_name", "usage_count", "selection_reason"],
            cleaned,
        )

        export_csv(
            OUT_DIR / "item_map_clean_usage_review.csv",
            ["loyverse_item_id", "loyverse_name", "qbo_item_id", "qbo_name", "usage_count", "keep"],
            review,
        )

        create_clean_usage_table(conn)
        load_clean_usage_table(conn, cleaned)

        print("Created/loaded item_map_clean_usage table.")
        print(f"Clean usage-based mappings inserted: {len(cleaned)}")
        print(f"Files written to: {OUT_DIR.resolve()}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()