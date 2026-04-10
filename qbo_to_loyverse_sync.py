import json
from database import get_conn
from qbo_client import QBOClient
from loyverse_client import LoyverseClient
from sync_event_store import (
    create_event,
    event_exists,
    get_state,
    set_state,
)


def get_item_map_by_qbo_item_id() -> dict[str, dict]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT loyverse_item_id, qbo_item_id, loyverse_name, qbo_name
            FROM item_map_clean_usage
        """)
        rows = cur.fetchall()

        result = {}
        for row in rows:
            qbo_id = str(row["qbo_item_id"]).strip()
            if qbo_id:
                result[qbo_id] = {
                    "loyverse_item_id": str(row["loyverse_item_id"]).strip(),
                    "loyverse_name": row["loyverse_name"],
                    "qbo_name": row["qbo_name"],
                }
        return result
    finally:
        conn.close()


def collect_qbo_events() -> int:
    qbo = QBOClient()

    last_txn_date = get_state("qbo_last_invoice_txn_date", "2000-01-01")
    sql = f"SELECT * FROM Invoice WHERE TxnDate >= '{last_txn_date}' MAXRESULTS 1000"
    data = qbo.query(sql)

    invoices = data.get("QueryResponse", {}).get("Invoice", [])
    new_count = 0
    newest_date = last_txn_date

    for inv in invoices:
        inv_id = str(inv.get("Id", "")).strip()
        doc_number = (inv.get("DocNumber") or "").strip()
        txn_date = (inv.get("TxnDate") or last_txn_date).strip()

        if not inv_id:
            continue

        if event_exists("QBO", "invoice", inv_id):
            continue

        create_event(
            source_system="QBO",
            event_type="invoice",
            source_event_id=inv_id,
            source_reference=doc_number,
            raw_payload=json.dumps(inv),
        )
        new_count += 1

        if txn_date > newest_date:
            newest_date = txn_date

    if newest_date:
        set_state("qbo_last_invoice_txn_date", newest_date)

    return new_count


def collect_qbo_item_qty_events() -> int:
    """
    Collect QBO item QtyOnHand changes and enqueue as events.

    This is the mechanism that makes stock increases/adjustments in QBO
    reflect into Loyverse near-live.
    """
    qbo = QBOClient()
    item_map = get_item_map_by_qbo_item_id()
    if not item_map:
        return 0

    qbo_items = qbo.get_all_items()
    qbo_idx = {str(it.get("Id", "")).strip(): it for it in qbo_items if str(it.get("Id", "")).strip()}

    new_count = 0

    for qbo_item_id, mapped in item_map.items():
        qbo_item = qbo_idx.get(str(qbo_item_id))
        if not qbo_item:
            continue

        qty_on_hand = qbo_item.get("QtyOnHand")
        try:
            qty_on_hand = float(qty_on_hand if qty_on_hand is not None else 0)
        except Exception:
            qty_on_hand = 0.0

        state_key = f"qbo_qty_on_hand:{qbo_item_id}"
        prev = get_state(state_key, "")
        try:
            prev_qty = float(prev) if prev not in ("", None) else None
        except Exception:
            prev_qty = None

        if prev_qty is not None and abs(prev_qty - qty_on_hand) < 0.0001:
            continue

        # unique per observed absolute qty
        source_event_id = f"{qbo_item_id}:{qty_on_hand}"
        if event_exists("QBO", "item_qty", source_event_id):
            set_state(state_key, str(qty_on_hand))
            continue

        create_event(
            source_system="QBO",
            event_type="item_qty",
            source_event_id=source_event_id,
            source_reference=qbo_item.get("Name") or "",
            item_id=str(mapped.get("loyverse_item_id") or ""),
            item_name=mapped.get("loyverse_name") or mapped.get("qbo_name") or "",
            quantity=qty_on_hand,
            raw_payload=json.dumps({
                "qbo_item_id": qbo_item_id,
                "qty_on_hand": qty_on_hand,
                "loyverse_item_id": mapped.get("loyverse_item_id"),
            }),
        )
        set_state(state_key, str(qty_on_hand))
        new_count += 1

    return new_count


def _is_loyverse_origin_receipt(doc_number: str) -> bool:
    return doc_number.upper().startswith("LOY-")


def _extract_receipt_lines(receipt_payload: dict) -> list[dict]:
    lines = receipt_payload.get("Line", [])
    if isinstance(lines, dict):
        lines = [lines]
    return lines if isinstance(lines, list) else []


def _extract_sales_item_line(line: dict) -> dict | None:
    if line.get("DetailType") != "SalesItemLineDetail":
        return None

    detail = line.get("SalesItemLineDetail", {}) or {}
    item_ref = detail.get("ItemRef", {}) or {}
    qbo_item_id = str(item_ref.get("value", "")).strip()
    qty = detail.get("Qty")

    if not qbo_item_id:
        return None

    try:
        qty = float(qty if qty is not None else 0)
    except Exception:
        qty = 0.0

    if qty == 0:
        return None

    return {
        "qbo_item_id": qbo_item_id,
        "qty": qty,
        "description": line.get("Description", "") or "",
    }


def process_qbo_event(event) -> dict:
    if event["event_type"] != "invoice":
        return {
            "status": "ignored",
            "reason": f"Unhandled QBO event type: {event['event_type']}",
        }

    invoice = json.loads(event["raw_payload"] or "{}")
    invoice_id = str(invoice.get("Id", "")).strip()
    doc_number = (invoice.get("DocNumber") or "").strip()

    # Prevent loop (anything starting with LOY came from Loyverse)
    if doc_number.upper().startswith("LOY-"):
        return {
            "status": "ignored",
            "reason": f"Invoice {doc_number} originated from Loyverse sync.",
        }

    item_map = get_item_map_by_qbo_item_id()
    lines = invoice.get("Line", [])
    created_children = 0

    for idx, line in enumerate(lines, start=1):
        if line.get("DetailType") != "SalesItemLineDetail":
            continue

        detail = line.get("SalesItemLineDetail", {})
        item_ref = detail.get("ItemRef", {})
        qbo_item_id = str(item_ref.get("value", "")).strip()

        if not qbo_item_id:
            continue

        qty = float(detail.get("Qty", 0))
        if qty == 0:
            continue

        mapped = item_map.get(qbo_item_id)
        if not mapped:
            continue

        loyverse_item_id = mapped["loyverse_item_id"]

        child_event_id = f"{invoice_id}:{idx}:{qbo_item_id}"

        if event_exists("QBO", "invoice_item", child_event_id):
            continue

        create_event(
            source_system="QBO",
            event_type="invoice_item",
            source_event_id=child_event_id,
            source_reference=doc_number,
            item_id=str(loyverse_item_id),
            item_name=mapped["loyverse_name"],
            quantity=qty * -1.0,
            raw_payload=json.dumps({
                "invoice_id": invoice_id,
                "qbo_item_id": qbo_item_id,
                "loyverse_item_id": loyverse_item_id,
                "qty_delta": qty * -1.0,
            }),
        )
        created_children += 1

    return {
        "status": "processed",
        "target_system": "LOYVERSE",
        "target_reference": f"QUEUED_{created_children}_ITEM_EVENTS",
    }


def process_qbo_item_event(event) -> dict:
    if event["event_type"] != "invoice_item":
        return {
            "status": "ignored",
            "reason": f"Unhandled QBO item event type: {event['event_type']}",
        }

    payload = json.loads(event["raw_payload"] or "{}")

    loyverse_item_id = str(payload.get("loyverse_item_id", "")).strip()
    qty_delta = float(payload.get("qty_delta") or 0)

    if not loyverse_item_id:
        return {
            "status": "failed",
            "error": "Missing loyverse_item_id in queued item event.",
        }

    if qty_delta == 0:
        return {
            "status": "ignored",
            "reason": "Quantity delta is zero.",
        }

    loy = LoyverseClient()
    variant_index = loy.build_item_variant_index()
    variant_info = variant_index.get(loyverse_item_id)

    if not variant_info:
        return {
            "status": "failed",
            "error": f"No Loyverse variant found for item_id {loyverse_item_id}.",
        }

    variant_id = variant_info["variant_id"]
    current_stock = float(variant_info.get("in_stock", 0))
    new_stock = current_stock + qty_delta

    if new_stock < 0:
        new_stock = 0.0

    print(
        f"LOYVERSE STOCK UPDATE | item_id={loyverse_item_id} | "
        f"variant_id={variant_id} | old={current_stock} | "
        f"delta={qty_delta} | new={new_stock}"
    )

    loy.update_inventory_levels([
        {
            "variant_id": variant_id,
            "in_stock": new_stock,
        }
    ])

    return {
        "status": "processed",
        "target_system": "LOYVERSE",
        "target_reference": f"variant:{variant_id}:stock:{new_stock}",
    }


def process_qbo_item_qty_event(event) -> dict:
    if event["event_type"] != "item_qty":
        return {
            "status": "ignored",
            "reason": f"Unhandled QBO item event type: {event['event_type']}",
        }

    payload = json.loads(event["raw_payload"] or "{}")
    loyverse_item_id = str(payload.get("loyverse_item_id", "")).strip()
    qty_on_hand = payload.get("qty_on_hand")

    if not loyverse_item_id:
        return {"status": "failed", "error": "Missing loyverse_item_id in item_qty payload."}

    try:
        qty_on_hand = float(qty_on_hand if qty_on_hand is not None else 0)
    except Exception:
        return {"status": "failed", "error": "Invalid qty_on_hand in item_qty payload."}

    if qty_on_hand < 0:
        qty_on_hand = 0.0

    loy = LoyverseClient()
    variant_index = loy.build_item_variant_index()
    variant_info = variant_index.get(loyverse_item_id)
    if not variant_info:
        return {
            "status": "failed",
            "error": f"No Loyverse variant found for item_id {loyverse_item_id}.",
        }

    variant_id = variant_info["variant_id"]
    current_stock = float(variant_info.get("in_stock", 0))
    new_stock = qty_on_hand

    print(
        f"LOYVERSE STOCK SET | item_id={loyverse_item_id} | "
        f"variant_id={variant_id} | old={current_stock} | new={new_stock}"
    )

    loy.update_inventory_levels([
        {"variant_id": variant_id, "in_stock": new_stock},
    ])

    return {
        "status": "processed",
        "target_system": "LOYVERSE",
        "target_reference": f"variant:{variant_id}:stock:{new_stock}",
    }