from datetime import date

from config import settings
from database import get_conn, init_db
from qbo_client import QBOClient


def normalize(text: str) -> str:
    text = (text or "").strip().lower()
    text = text.replace("&", "and")
    for ch in ["-", "_", "/", "\\"]:
        text = text.replace(ch, " ")
    return " ".join(text.split())


def load_mappings():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT loyverse_item_id, qbo_item_id, loyverse_name, qbo_name, sku
            FROM item_map
            WHERE qbo_environment = ?
            """,
            (settings.QBO_ENVIRONMENT,),
        )
        return cur.fetchall()
    finally:
        conn.close()


def update_mapping_qbo_item_id(loyverse_item_id: str, new_qbo_item_id: str, new_qbo_name: str, source: str):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE item_map
            SET qbo_item_id = ?,
                qbo_name = ?,
                last_source = ?,
                last_synced_at = CURRENT_TIMESTAMP
            WHERE loyverse_item_id = ?
              AND qbo_environment = ?
            """,
            (str(new_qbo_item_id), new_qbo_name or "", source, str(loyverse_item_id), settings.QBO_ENVIRONMENT),
        )
        conn.commit()
    finally:
        conn.close()


def choose_unique_name(base: str, existing_names: set[str]) -> str:
    if base not in existing_names:
        return base
    for i in range(2, 500):
        cand = f"{base} ({i})"
        if cand not in existing_names:
            return cand
    raise Exception(f"Could not find a unique QBO item Name for base='{base}'")


def is_inventory_tracked(item: dict) -> bool:
    return bool(item.get("TrackQtyOnHand")) or (item.get("Type") == "Inventory")


def main():
    init_db()
    qbo = QBOClient()

    income = qbo.get_first_income_account()
    cogs = qbo.get_first_cogs_account()
    asset = qbo.get_first_inventory_asset_account()

    if not income:
        raise Exception("No Income account found in QBO.")
    if not cogs:
        raise Exception("No COGS account found in QBO.")
    if not asset:
        raise Exception("No Inventory Asset account found in QBO.")

    income_id = income[0]["Id"]
    cogs_id = cogs[0]["Id"]
    asset_id = asset[0]["Id"]

    all_items = qbo.get_all_items()
    by_id = {str(it.get("Id")): it for it in all_items if str(it.get("Id", "")).strip()}
    by_sku = {}
    by_name = {}
    existing_names = set()
    for it in all_items:
        name = (it.get("Name") or "").strip()
        if name:
            existing_names.add(name)
            by_name.setdefault(normalize(name), []).append(it)
        sku = (it.get("Sku") or "").strip().upper()
        if sku:
            by_sku.setdefault(sku, []).append(it)

    mappings = load_mappings()
    print(f"Mappings loaded: {len(mappings)} (env={settings.QBO_ENVIRONMENT})")
    print(f"Dry-run: {settings.DRY_RUN}")

    converted = 0
    remapped = 0
    skipped = 0
    failed = 0

    for row in mappings:
        loy_id = str(row["loyverse_item_id"])
        qbo_id = str(row["qbo_item_id"])
        loy_name = row["loyverse_name"] or ""
        sku = (row["sku"] or "").strip().upper()

        qbo_item = by_id.get(qbo_id)
        if not qbo_item:
            print(f"FAILED: missing QBO item id={qbo_id} for loyverse_item_id={loy_id} ({loy_name})")
            failed += 1
            continue

        if is_inventory_tracked(qbo_item):
            skipped += 1
            continue

        # If an inventory-tracked sibling exists, remap instead of creating.
        candidates = []
        if sku and sku in by_sku:
            candidates = by_sku[sku]
        if not candidates:
            candidates = by_name.get(normalize(qbo_item.get("Name") or loy_name), [])

        inv_candidates = [c for c in candidates if is_inventory_tracked(c)]
        if inv_candidates:
            chosen = sorted(inv_candidates, key=lambda x: int(x["Id"]) if str(x.get("Id", "")).isdigit() else 10**18)[0]
            new_id = str(chosen["Id"])
            print(f"REMAP: {loy_name} | {qbo_id} -> {new_id} (existing inventory item)")
            if not settings.DRY_RUN:
                update_mapping_qbo_item_id(loy_id, new_id, chosen.get("Name") or "", "remapped_to_existing_inventory_item")
            remapped += 1
            continue

        # Create a new inventory-tracked item with a unique name.
        base_name = (qbo_item.get("Name") or loy_name or "Unnamed Item").strip()[:100]
        inv_name = choose_unique_name(f"{base_name} [INV]", existing_names)[:100]
        existing_names.add(inv_name)

        payload = {
            "Name": inv_name,
            "Type": "Inventory",
            "IncomeAccountRef": {"value": str(income_id)},
            "ExpenseAccountRef": {"value": str(cogs_id)},
            "AssetAccountRef": {"value": str(asset_id)},
            "TrackQtyOnHand": True,
            "QtyOnHand": 0,
            "InvStartDate": date.today().isoformat(),
        }
        if sku:
            payload["Sku"] = sku

        print(f"CREATE INVENTORY: {loy_name} | old={qbo_id} -> new_name='{inv_name}'")
        if settings.DRY_RUN:
            converted += 1
            continue

        result = qbo.create_item(payload)
        new_item = result.get("Item", {})
        new_id = str(new_item.get("Id", "")).strip()
        if not new_id:
            print(f"FAILED: did not receive new QBO item Id for {loy_name}")
            failed += 1
            continue

        update_mapping_qbo_item_id(loy_id, new_id, new_item.get("Name") or inv_name, "created_inventory_item")
        converted += 1

    print(f"\nDone. Converted(created): {converted}, Remapped(existing): {remapped}, Skipped(already inventory): {skipped}, Failed: {failed}")


if __name__ == "__main__":
    main()

