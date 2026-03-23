import csv
from datetime import datetime
from pathlib import Path

from config import settings
from database import get_conn, init_db
from loyverse_client import LoyverseClient
from qbo_client import QBOClient
from approval_store import init_approval_tables, create_batch, add_reconcile_item


REPORTS_DIR = Path("reports")
REPORTS_DIR.mkdir(exist_ok=True)


def safe_float(value, default=0.0):
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def get_loyverse_stock_qty(item: dict) -> float:
    for key in [
        "in_stock",
        "stock",
        "quantity",
        "available_quantity",
        "current_stock",
        "inventory_count",
    ]:
        if key in item:
            return safe_float(item.get(key), 0.0)
    return 0.0


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


def build_loyverse_index(items: list[dict]) -> dict[str, dict]:
    idx = {}
    for item in items:
        item_id = item.get("id")
        if item_id:
            idx[item_id] = item
    return idx


def build_qbo_index(items: list[dict]) -> dict[str, dict]:
    idx = {}
    for item in items:
        item_id = str(item.get("Id", "")).strip()
        if item_id:
            idx[item_id] = item
    return idx


def generate_inventory_report() -> tuple[str, str]:
    init_db()
    init_approval_tables()

    loy = LoyverseClient()
    qbo = QBOClient()

    loy_items_raw = loy.get_items()
    loy_items = loy_items_raw.get("items", []) if isinstance(loy_items_raw, dict) else []

    qbo_items_raw = qbo.get_items()
    qbo_items = qbo_items_raw.get("QueryResponse", {}).get("Item", [])

    item_map_rows = load_item_map()

    loy_idx = build_loyverse_index(loy_items)
    qbo_idx = build_qbo_index(qbo_items)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    batch_id = f"{settings.QBO_ENVIRONMENT}_{timestamp}"
    report_path = REPORTS_DIR / f"inventory_reconcile_{batch_id}.csv"

    create_batch(batch_id, settings.QBO_ENVIRONMENT)

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
                "suggested_action",
                "approved_action",
            ],
        )
        writer.writeheader()

        for row in item_map_rows:
            loy_item_id = row["loyverse_item_id"]
            qbo_item_id = str(row["qbo_item_id"])
            item_name = row["loyverse_name"] or row["qbo_name"] or ""

            loy_item = loy_idx.get(loy_item_id, {})
            qbo_item = qbo_idx.get(qbo_item_id, {})

            loy_qty = get_loyverse_stock_qty(loy_item)
            qbo_qty = safe_float(qbo_item.get("QtyOnHand"), 0.0)
            diff = loy_qty - qbo_qty

            if abs(diff) < 0.0001:
                continue

            suggested_action = "LOYVERSE"

            writer.writerow({
                "loyverse_item_id": loy_item_id,
                "qbo_item_id": qbo_item_id,
                "item_name": item_name,
                "loyverse_qty": loy_qty,
                "qbo_qty": qbo_qty,
                "difference": diff,
                "suggested_action": suggested_action,
                "approved_action": "",
            })

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

    return str(report_path), batch_id