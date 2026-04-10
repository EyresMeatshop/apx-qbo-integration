import csv
from collections import defaultdict
from pathlib import Path

OUT_DIR = Path("mapping_audit")
OUT_DIR.mkdir(exist_ok=True)

from database import get_conn, db_backend


def fetch_all_item_map(conn):
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


def export_csv(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def build_conflict_reports(rows):
    by_loyverse = defaultdict(list)
    by_qbo = defaultdict(list)

    for r in rows:
        loy_key = (r["loyverse_item_id"], r["loyverse_name"])
        qbo_key = (r["qbo_item_id"], r["qbo_name"])
        by_loyverse[loy_key].append(r)
        by_qbo[qbo_key].append(r)

    loyverse_duplicates = []
    for (loy_id, loy_name), items in by_loyverse.items():
        qbo_ids = sorted({str(x["qbo_item_id"]) for x in items})
        qbo_names = sorted({str(x["qbo_name"]) for x in items})
        if len(qbo_ids) > 1:
            loyverse_duplicates.append({
                "loyverse_item_id": loy_id,
                "loyverse_name": loy_name,
                "qbo_item_count": len(qbo_ids),
                "qbo_item_ids": " | ".join(qbo_ids),
                "qbo_names": " | ".join(qbo_names),
            })

    qbo_duplicates = []
    for (qbo_id, qbo_name), items in by_qbo.items():
        loy_ids = sorted({str(x["loyverse_item_id"]) for x in items})
        loy_names = sorted({str(x["loyverse_name"]) for x in items})
        if len(loy_ids) > 1:
            qbo_duplicates.append({
                "qbo_item_id": qbo_id,
                "qbo_name": qbo_name,
                "loyverse_item_count": len(loy_ids),
                "loyverse_item_ids": " | ".join(loy_ids),
                "loyverse_names": " | ".join(loy_names),
            })

    return loyverse_duplicates, qbo_duplicates, by_loyverse, by_qbo


def choose_canonical_mapping(by_loyverse):
    """
    Safe default rule:
    - keep exactly one QBO row per Loyverse item
    - prefer the lowest numeric qbo_item_id if possible
    - otherwise lexicographically smallest qbo_item_id
    """
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
                "selection_reason": "only_mapping",
            })
            continue

        def sort_key(row):
            q = str(row["qbo_item_id"])
            return (0, int(q)) if q.isdigit() else (1, q)

        sorted_items = sorted(items, key=sort_key)
        chosen = sorted_items[0]

        cleaned.append({
            "loyverse_item_id": chosen["loyverse_item_id"],
            "loyverse_name": chosen["loyverse_name"],
            "qbo_item_id": chosen["qbo_item_id"],
            "qbo_name": chosen["qbo_name"],
            "selection_reason": "lowest_qbo_item_id",
        })

        for r in sorted_items:
            review.append({
                "loyverse_item_id": r["loyverse_item_id"],
                "loyverse_name": r["loyverse_name"],
                "qbo_item_id": r["qbo_item_id"],
                "qbo_name": r["qbo_name"],
                "keep": "YES" if r["qbo_item_id"] == chosen["qbo_item_id"] else "NO",
            })

    return cleaned, review


def create_clean_table(conn):
    cur = conn.cursor()
    if db_backend() == "postgres":
        cur.execute("""
            CREATE TABLE IF NOT EXISTS item_map_clean (
                id SERIAL PRIMARY KEY,
                loyverse_item_id TEXT NOT NULL,
                loyverse_name TEXT,
                qbo_item_id TEXT NOT NULL,
                qbo_name TEXT,
                selection_reason TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
    else:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS item_map_clean (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                loyverse_item_id TEXT NOT NULL,
                loyverse_name TEXT,
                qbo_item_id TEXT NOT NULL,
                qbo_name TEXT,
                selection_reason TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
    cur.execute("DELETE FROM item_map_clean")
    conn.commit()


def load_clean_table(conn, cleaned_rows):
    cur = conn.cursor()
    cur.executemany("""
        INSERT INTO item_map_clean (
            loyverse_item_id, loyverse_name, qbo_item_id, qbo_name, selection_reason
        )
        VALUES (?, ?, ?, ?, ?)
    """, [
        (
            r["loyverse_item_id"],
            r["loyverse_name"],
            r["qbo_item_id"],
            r["qbo_name"],
            r["selection_reason"],
        )
        for r in cleaned_rows
    ])
    conn.commit()


def main():
    conn = get_conn()
    try:
        rows = fetch_all_item_map(conn)

        print(f"Total item_map rows: {len(rows)}")
        unique_loyverse = len({(r['loyverse_item_id'], r['loyverse_name']) for r in rows})
        unique_qbo = len({(r['qbo_item_id'], r['qbo_name']) for r in rows})
        print(f"Unique Loyverse items: {unique_loyverse}")
        print(f"Unique QBO items: {unique_qbo}")

        loyverse_duplicates, qbo_duplicates, by_loyverse, by_qbo = build_conflict_reports(rows)

        print(f"Loyverse items with multiple QBO mappings: {len(loyverse_duplicates)}")
        print(f"QBO items with multiple Loyverse mappings: {len(qbo_duplicates)}")

        export_csv(
            OUT_DIR / "all_item_map_rows.csv",
            ["map_rowid", "loyverse_item_id", "loyverse_name", "qbo_item_id", "qbo_name"],
            [dict(r) for r in rows],
        )

        export_csv(
            OUT_DIR / "duplicate_loyverse_to_qbo.csv",
            ["loyverse_item_id", "loyverse_name", "qbo_item_count", "qbo_item_ids", "qbo_names"],
            loyverse_duplicates,
        )

        export_csv(
            OUT_DIR / "duplicate_qbo_to_loyverse.csv",
            ["qbo_item_id", "qbo_name", "loyverse_item_count", "loyverse_item_ids", "loyverse_names"],
            qbo_duplicates,
        )

        cleaned, review = choose_canonical_mapping(by_loyverse)

        export_csv(
            OUT_DIR / "item_map_clean_preview.csv",
            ["loyverse_item_id", "loyverse_name", "qbo_item_id", "qbo_name", "selection_reason"],
            cleaned,
        )

        export_csv(
            OUT_DIR / "item_map_clean_review.csv",
            ["loyverse_item_id", "loyverse_name", "qbo_item_id", "qbo_name", "keep"],
            review,
        )

        create_clean_table(conn)
        load_clean_table(conn, cleaned)

        print("Created/loaded item_map_clean table.")
        print(f"Clean mappings inserted: {len(cleaned)}")
        print(f"Files written to: {OUT_DIR.resolve()}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()