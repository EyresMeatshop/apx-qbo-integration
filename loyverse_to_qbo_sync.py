import json
from datetime import datetime

from loyverse_client import LoyverseClient
from qbo_client import QBOClient
import sync_receipts_loyverse_to_qbo
from sync_event_store import (
    create_event,
    event_exists,
    get_state,
    set_state,
)


def _parse_iso_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    # Loyverse commonly returns ISO timestamps; tolerate trailing Z.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def collect_loyverse_events() -> int:
    loy = LoyverseClient()

    # Important:
    # - Paginate receipts until we're confident we've scanned everything newer than our watermark.
    # - Do NOT assume strict "newest-first" ordering across pages; stopping at the first matching id
    #   can skip sales if ordering isn't guaranteed.

    last_watermark = get_state("loyverse_last_receipt_created_at", "")
    last_watermark_dt = _parse_iso_dt(last_watermark)

    new_count = 0

    newest_seen_created_at = last_watermark
    newest_seen_id = get_state("loyverse_last_receipt_id", "")
    newest_seen_number = get_state("loyverse_last_receipt_number", "")

    cursor = None
    pages = 0

    while True:
        pages += 1
        if pages > 200:
            # Safety valve: avoid infinite loops if API misbehaves.
            print(f"WARNING: Loyverse receipts pagination exceeded {pages} pages; stopping early.")
            break

        params = {"limit": 250}
        if cursor:
            params["cursor"] = cursor

        data = loy._get("/receipts", params=params)
        receipts = data.get("receipts", []) if isinstance(data, dict) else []
        if not receipts:
            break

        stop_page = False

        # Capture "newest head" from the first page only (best-effort; used for watermark progression).
        if pages == 1 and receipts:
            head = receipts[0]
            head_id = head.get("id") or head.get("receipt_number") or head.get("receipt_no") or ""
            head_num = head.get("receipt_number") or head.get("receipt_no") or ""
            head_created_raw = (
                head.get("created_at")
                or head.get("createdAt")
                or head.get("closed_at")
                or head.get("closedAt")
                or head.get("updated_at")
                or head.get("updatedAt")
                or ""
            )
            if head_id:
                newest_seen_id = str(head_id)
            if head_num:
                newest_seen_number = str(head_num)
            if head_created_raw:
                newest_seen_created_at = str(head_created_raw).strip()

        for receipt in receipts:
            receipt_number = receipt.get("receipt_number") or receipt.get("receipt_no") or ""
            receipt_id = receipt.get("id") or receipt_number

            if not receipt_id:
                continue

            created_raw = (
                receipt.get("created_at")
                or receipt.get("createdAt")
                or receipt.get("closed_at")
                or receipt.get("closedAt")
                or receipt.get("updated_at")
                or receipt.get("updatedAt")
                or ""
            )
            created_dt = _parse_iso_dt(str(created_raw) if created_raw is not None else None)

            # Watermark gate: once we're at/before the last processed time, stop scanning further pages.
            if last_watermark_dt and created_dt and created_dt <= last_watermark_dt:
                stop_page = True
                break

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

        cursor = data.get("cursor") if isinstance(data, dict) else None

        if stop_page or not cursor:
            break

    if newest_seen_created_at:
        set_state("loyverse_last_receipt_created_at", newest_seen_created_at)
    if newest_seen_id:
        set_state("loyverse_last_receipt_id", newest_seen_id)
    if newest_seen_number:
        set_state("loyverse_last_receipt_number", newest_seen_number)

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