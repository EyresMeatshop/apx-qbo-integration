from datetime import datetime

from loyverse_client import LoyverseClient
from qbo_client import QBOClient
from database import get_conn, init_db
from config import settings


def normalize(text):
    return (text or "").strip().lower()


def get_price(loy_item):
    for key in ["price", "default_price", "sell_price"]:
        value = loy_item.get(key)
        if value not in (None, ""):
            try:
                return float(value)
            except Exception:
                pass
    return 0.0


def build_qbo_item(loy_item, income_account_id):
    name = loy_item.get("item_name") or loy_item.get("name") or "Unnamed Item"
    sku = loy_item.get("sku") or ""
    price = get_price(loy_item)

    payload = {
        "Name": name[:100],
        "Type": "NonInventory",
        "IncomeAccountRef": {"value": str(income_account_id)},
        "UnitPrice": price,
        "TrackQtyOnHand": False,
    }

    if sku:
        payload["Sku"] = sku

    return payload


def save_mapping(loy_item, qbo_item, source="loyverse_to_qbo", match_method="name"):
    conn = get_conn()
    cur = conn.cursor()

    loy_id = loy_item.get("id")
    loy_name = loy_item.get("item_name") or loy_item.get("name") or ""
    sku = loy_item.get("sku") or ""

    qbo_id = qbo_item.get("Id")
    qbo_name = qbo_item.get("Name") or ""

    # remove any prior mapping for same loyverse item in same environment
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
        source,
        datetime.utcnow().isoformat()
    ))

    conn.commit()
    conn.close()


def main():
    init_db()

    loy = LoyverseClient()
    qbo = QBOClient()

    income_accounts = qbo.get_first_income_account()
    if not income_accounts:
        raise Exception("No Income account found in QBO sandbox.")

    income_account_id = income_accounts[0]["Id"]
    print("Using Income Account ID:", income_account_id)

    loy_items_raw = loy.get_items()
    loy_items = loy_items_raw.get("items", []) if isinstance(loy_items_raw, dict) else []

    qbo_items_raw = qbo.get_items()
    qbo_items = qbo_items_raw.get("QueryResponse", {}).get("Item", [])

    qbo_by_name = {
        normalize(item.get("Name")): item
        for item in qbo_items
    }

    print(f"Found {len(loy_items)} Loyverse items")
    print(f"Found {len(qbo_items)} QBO items")

    created = 0
    skipped = 0
    failed = 0

    for loy_item in loy_items:
        loy_name = loy_item.get("item_name") or loy_item.get("name") or ""
        existing_qbo = qbo_by_name.get(normalize(loy_name))

        try:
            if existing_qbo:
                save_mapping(
                    loy_item,
                    existing_qbo,
                    source="matched_existing_qbo",
                    match_method="name"
                )
                print(f"SKIPPED (already exists): {loy_name} -> QBO ID {existing_qbo.get('Id')}")
                skipped += 1
                continue

            payload = build_qbo_item(loy_item, income_account_id)
            result = qbo.create_item(payload)
            qbo_item = result.get("Item", {})
            save_mapping(
                loy_item,
                qbo_item,
                source="created_in_qbo",
                match_method="name"
            )

            print(f"CREATED: {payload['Name']} -> QBO ID {qbo_item.get('Id')}")
            created += 1

        except Exception as e:
            print(f"FAILED: {loy_name} -> {e}")
            failed += 1

    print(f"\nDone. Created: {created}, Skipped: {skipped}, Failed: {failed}")


if __name__ == "__main__":
    main()