from datetime import datetime
import re

from loyverse_client import LoyverseClient
from qbo_client import QBOClient
from database import get_conn, init_db
from config import settings


def normalize_name(text):
    text = (text or "").strip().lower()
    text = text.replace("&", "and")
    text = re.sub(r"[-_/]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def save_mapping(loy_item, qbo_item, match_method):
    conn = get_conn()
    cur = conn.cursor()

    loy_id = loy_item.get("id")
    loy_name = loy_item.get("item_name") or loy_item.get("name") or ""
    sku = loy_item.get("sku") or ""

    qbo_id = qbo_item.get("Id")
    qbo_name = qbo_item.get("Name") or ""

    cur.execute("""
        DELETE FROM item_map
        WHERE loyverse_item_id = ?
          AND qbo_environment = ?
    """, (loy_id, settings.QBO_ENVIRONMENT))

    cur.execute("""
        INSERT INTO item_map
        (loyverse_item_id, loyverse_variant_id, qbo_item_id, sku, loyverse_name, qbo_name,
         qbo_environment, match_method, last_source, last_synced_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        loy_id,
        None,
        qbo_id,
        sku,
        loy_name,
        qbo_name,
        settings.QBO_ENVIRONMENT,
        match_method,
        "live_safe_match",
        datetime.utcnow().isoformat()
    ))

    conn.commit()
    conn.close()


def already_mapped(loy_item_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT 1
        FROM item_map
        WHERE loyverse_item_id = ?
          AND qbo_environment = ?
        LIMIT 1
    """, (loy_item_id, settings.QBO_ENVIRONMENT))

    row = cur.fetchone()
    conn.close()
    return row is not None


def main():
    init_db()

    loy = LoyverseClient()
    qbo = QBOClient()

    loy_items_raw = loy.get_items()
    loy_items = loy_items_raw.get("items", []) if isinstance(loy_items_raw, dict) else []

    qbo_items_raw = qbo.get_items()
    qbo_items = qbo_items_raw.get("QueryResponse", {}).get("Item", [])

    print(f"Environment: {settings.QBO_ENVIRONMENT}")
    print(f"Dry run: {settings.DRY_RUN}")
    print(f"Loyverse items found: {len(loy_items)}")
    print(f"QBO items found: {len(qbo_items)}")

    qbo_by_sku = {}
    qbo_by_name = {}

    for item in qbo_items:
        sku = (item.get("Sku") or "").strip().lower()
        name = normalize_name(item.get("Name"))

        if sku:
            qbo_by_sku[sku] = item
        if name:
            qbo_by_name[name] = item

    matched_sku = 0
    matched_name = 0
    no_match = 0
    skipped_existing = 0

    for loy_item in loy_items:
        loy_id = loy_item.get("id")
        loy_name = loy_item.get("item_name") or loy_item.get("name") or ""
        loy_sku = (loy_item.get("sku") or "").strip().lower()

        if already_mapped(loy_id):
            print(f"ALREADY MAPPED: {loy_name}")
            skipped_existing += 1
            continue

        match = None
        match_method = None

        if loy_sku and loy_sku in qbo_by_sku:
            match = qbo_by_sku[loy_sku]
            match_method = "sku"
        else:
            norm_name = normalize_name(loy_name)
            if norm_name in qbo_by_name:
                match = qbo_by_name[norm_name]
                match_method = "name"

        if match:
            if not settings.DRY_RUN:
                save_mapping(loy_item, match, match_method)
            print(f"MATCHED ({match_method.upper()}): Loyverse '{loy_name}' -> QBO '{match.get('Name')}'")
            if match_method == "sku":
                matched_sku += 1
            else:
                matched_name += 1
        else:
            print(f"NO MATCH: Loyverse '{loy_name}'")
            no_match += 1

    print("\nDone.")
    print(f"Matched by SKU: {matched_sku}")
    print(f"Matched by Name: {matched_name}")
    print(f"Already mapped: {skipped_existing}")
    print(f"No match: {no_match}")


if __name__ == "__main__":
    main()