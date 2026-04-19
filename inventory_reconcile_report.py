import csv
from datetime import datetime
from pathlib import Path

from config import settings
from database import get_conn, init_db
from loyverse_client import LoyverseClient, normalize_loyverse_item_id
from qbo_client import QBOClient
from approval_store import init_approval_tables, create_batch, add_reconcile_item


REPORTS_DIR = Path("reports")
REPORTS_DIR.mkdir(exist_ok=True)

# QBO is the source of truth for on-hand quantity. Rows here are mismatches where
# Loyverse variant stock != QBO QtyOnHand. Suggested action LOYVERSE = update Loyverse to qbo_qty.

QTY_EPSILON = 1e-4


def safe_float(value, default=0.0):
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def load_item_map():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT loyverse_item_id, qbo_item_id, loyverse_name, qbo_name
            FROM item_map
            WHERE qbo_environment = ?
        """, (settings.QBO_ENVIRONMENT,))
        return cur.fetchall()
    finally:
        conn.close()


def count_item_map_rows() -> int:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*) AS c
            FROM item_map
            WHERE qbo_environment = ?
            """,
            (settings.QBO_ENVIRONMENT,),
        )
        row = cur.fetchone()
        return int(row["c"] if isinstance(row, dict) else row[0])
    finally:
        conn.close()


def build_qbo_index(items: list[dict]) -> dict[str, dict]:
    idx = {}
    for item in items:
        item_id = str(item.get("Id", "")).strip()
        if item_id:
            idx[item_id] = item
    return idx


def generate_inventory_report() -> tuple[str, str, int, int]:
    """
    Compare mapped items: Loyverse variant on-hand vs QBO QtyOnHand.

    Returns (report_path, batch_id, mismatch_count, mapped_pairs_count).
    Only rows in ``item_map`` for ``QBO_ENVIRONMENT`` are compared; unmapped items never appear.
    """
    init_db()
    init_approval_tables()

    loy = LoyverseClient()
    qbo = QBOClient()

    # Same variant-level stock used for inventory API updates (not raw item fields).
    variant_index = loy.build_item_variant_index()

    qbo_items_list = qbo.get_all_items()
    qbo_idx = build_qbo_index(qbo_items_list)

    item_map_rows = load_item_map()
    mapped_pairs_count = len(item_map_rows)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    batch_id = f"{settings.QBO_ENVIRONMENT}_{timestamp}"
    report_path = REPORTS_DIR / f"inventory_reconcile_{batch_id}.csv"

    create_batch(batch_id, settings.QBO_ENVIRONMENT)

    mismatch_count = 0
    compared_equal = 0

    with report_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "loyverse_item_id",
                "qbo_item_id",
                "item_name",
                "loyverse_qty",
                "qbo_qty",
                "difference",
                "source_of_truth",
                "suggested_action",
                "recommended_fix",
                "approved_action",
            ],
        )
        writer.writeheader()

        for row in item_map_rows:
            loy_item_id = row["loyverse_item_id"]
            qbo_item_id = str(row["qbo_item_id"])
            item_name = row["loyverse_name"] or row["qbo_name"] or ""

            vinfo = variant_index.get(normalize_loyverse_item_id(loy_item_id)) or {}
            loy_qty = safe_float(vinfo.get("in_stock"), 0.0)

            qbo_item = qbo_idx.get(qbo_item_id, {})
            qbo_qty = safe_float(qbo_item.get("QtyOnHand"), 0.0)

            diff = loy_qty - qbo_qty

            if abs(diff) < QTY_EPSILON:
                compared_equal += 1
                continue

            mismatch_count += 1
            suggested_action = "LOYVERSE"

            writer.writerow(
                {
                    "loyverse_item_id": loy_item_id,
                    "qbo_item_id": qbo_item_id,
                    "item_name": item_name,
                    "loyverse_qty": loy_qty,
                    "qbo_qty": qbo_qty,
                    "difference": diff,
                    "source_of_truth": "QBO",
                    "suggested_action": suggested_action,
                    "recommended_fix": "Set Loyverse stock to QBO QtyOnHand when approved",
                    "approved_action": "",
                }
            )

            add_reconcile_item(
                batch_id=batch_id,
                loyverse_item_id=loy_item_id,
                qbo_item_id=qbo_item_id,
                item_name=item_name,
                loyverse_qty=loy_qty,
                qbo_qty=qbo_qty,
                difference=diff,
                suggested_action=suggested_action,
            )

    print(
        f"Inventory reconcile: environment={settings.QBO_ENVIRONMENT} | "
        f"item_map pairs={mapped_pairs_count} | "
        f"matching qty (no row)={compared_equal} | "
        f"mismatches written={mismatch_count}"
    )
    if mapped_pairs_count == 0:
        print(
            "WARNING: item_map has no rows for this QBO environment. "
            "Run: python sync_items_loyverse_to_qbo.py (Render Shell) to build mappings, then rerun nightly."
        )

    return str(report_path), batch_id, mismatch_count, mapped_pairs_count
