import json
from loyverse_client import LoyverseClient
from qbo_client import QBOClient
import sync_receipts_loyverse_to_qbo
from sync_event_store import (
    create_event,
    event_exists,
    get_state,
    set_state,
)


def collect_loyverse_events() -> int:
    loy = LoyverseClient()

    # Only fetch the newest page for live sync.
    data = loy._get("/receipts", params={})
    receipts = data.get("receipts", []) if isinstance(data, dict) else []

    last_receipt_number = get_state("loyverse_last_receipt_number", "")
    new_count = 0
    newest_seen = last_receipt_number

    for receipt in receipts:
        receipt_number = receipt.get("receipt_number") or receipt.get("receipt_no") or ""
        receipt_id = receipt.get("id") or receipt_number

        if not receipt_id:
            continue

        if event_exists("LOYVERSE", "sale_receipt", str(receipt_id)):
            continue

        create_event(
            source_system="LOYVERSE",
            event_type="sale_receipt",
            source_event_id=str(receipt_id),
            source_reference=receipt_number,
            raw_payload=json.dumps(receipt),
        )
        new_count += 1

        if receipt_number and receipt_number > newest_seen:
            newest_seen = receipt_number

    if newest_seen:
        set_state("loyverse_last_receipt_number", newest_seen)

    return new_count


def process_loyverse_event(event) -> dict:
    event_type = event["event_type"]

    if event_type == "sale_receipt":
        receipt = json.loads(event["raw_payload"] or "{}")
        receipt_id = sync_receipts_loyverse_to_qbo.get_receipt_id(receipt)
        receipt_number = sync_receipts_loyverse_to_qbo.get_receipt_number(receipt)
        total_amount = sync_receipts_loyverse_to_qbo.get_receipt_total(receipt)

        if sync_receipts_loyverse_to_qbo.receipt_already_synced(receipt_id):
            return {"status": "ignored", "reason": f"Receipt {receipt_number} already synced (DB)."}

        qbo = QBOClient()
        doc_number = f"LOY-{receipt_number}"
        existing = qbo.get_sales_receipt_by_doc_number(doc_number)
        existing_rows = existing.get("QueryResponse", {}).get("SalesReceipt", [])

        if existing_rows:
            qbo_id = existing_rows[0].get("Id")
            sync_receipts_loyverse_to_qbo.save_receipt_map(receipt_id, qbo_id, receipt_number, total_amount)
            return {
                "status": "processed",
                "target_system": "QBO",
                "target_reference": f"SalesReceipt:{qbo_id}",
            }

        item_map = sync_receipts_loyverse_to_qbo.load_item_map()
        payload = sync_receipts_loyverse_to_qbo.build_qbo_sales_receipt(receipt, item_map)
        result = qbo.create_sales_receipt(payload)
        qbo_sales_receipt = result.get("SalesReceipt", {})
        qbo_id = qbo_sales_receipt.get("Id")

        sync_receipts_loyverse_to_qbo.save_receipt_map(receipt_id, qbo_id, receipt_number, total_amount)

        return {
            "status": "processed",
            "target_system": "QBO",
            "target_reference": f"SalesReceipt:{qbo_id}",
        }

    return {
        "status": "ignored",
        "reason": f"Unhandled Loyverse event type: {event_type}",
    }