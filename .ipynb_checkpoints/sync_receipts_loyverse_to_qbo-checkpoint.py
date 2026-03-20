from datetime import datetime

from loyverse_client import LoyverseClient
from qbo_client import QBOClient
from database import get_conn, init_db
from config import settings

import re

def normalize_name(text):
    text = (text or "").strip().lower()
    text = text.replace("&", "and")
    text = re.sub(r"[-_/]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def normalize(text):
    return (text or "").strip().lower()


def load_item_map():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT loyverse_item_id, qbo_item_id, loyverse_name, qbo_name
        FROM item_map
        WHERE qbo_environment = ?
    """, (settings.QBO_ENVIRONMENT,))

    rows = cur.fetchall()
    conn.close()

    by_id = {}
    by_name = {}

    for row in rows:
        record = {
            "qbo_item_id": row["qbo_item_id"],
            "loyverse_name": row["loyverse_name"],
            "qbo_name": row["qbo_name"],
        }

        by_id[row["loyverse_item_id"]] = record

        loy_name = normalize_name(row["loyverse_name"])
        if loy_name:
            by_name[loy_name] = record

    return {
        "by_id": by_id,
        "by_name": by_name,
    }


def receipt_already_synced(loyverse_receipt_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT 1
        FROM receipt_map
        WHERE loyverse_receipt_id = ?
          AND qbo_environment = ?
        LIMIT 1
    """, (loyverse_receipt_id, settings.QBO_ENVIRONMENT))

    row = cur.fetchone()
    conn.close()
    return row is not None


def save_receipt_map(loyverse_receipt_id, qbo_sales_receipt_id, receipt_number, total_amount):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO receipt_map
        (loyverse_receipt_id, qbo_sales_receipt_id, qbo_environment,
         loyverse_receipt_number, total_amount, synced_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        loyverse_receipt_id,
        qbo_sales_receipt_id,
        settings.QBO_ENVIRONMENT,
        receipt_number,
        str(total_amount),
        datetime.utcnow().isoformat()
    ))

    conn.commit()
    conn.close()


def get_receipt_lines(receipt):
    for key in ["line_items", "receipt_lines", "items"]:
        value = receipt.get(key)
        if isinstance(value, list):
            return value
    return []


def get_receipt_id(receipt):
    return receipt.get("id") or receipt.get("receipt_id") or receipt.get("receipt_number")


def get_receipt_number(receipt):
    return (
        receipt.get("receipt_number")
        or receipt.get("receipt_no")
        or receipt.get("number")
        or str(get_receipt_id(receipt))
    )


def get_receipt_total(receipt):
    for key in ["total_money", "total", "total_amount"]:
        value = receipt.get(key)
        if value not in (None, ""):
            try:
                return float(value)
            except Exception:
                pass
    return 0.0


def get_receipt_date(receipt):
    for key in ["created_at", "closed_at", "date"]:
        value = receipt.get(key)
        if value:
            return str(value)[:10]
    return None


def get_line_loyverse_item_id(line):
    for key in ["item_id", "variant_id", "id"]:
        value = line.get(key)
        if value:
            return value
    return None


def get_line_name(line):
    return (
        line.get("item_name")
        or line.get("name")
        or line.get("description")
        or "Unnamed Item"
    )


def get_line_qty(line):
    for key in ["quantity", "qty"]:
        value = line.get(key)
        if value not in (None, ""):
            try:
                return float(value)
            except Exception:
                pass
    return 1.0


def get_line_price(line):
    for key in ["price", "price_money", "unit_price"]:
        value = line.get(key)
        if value not in (None, ""):
            try:
                return float(value)
            except Exception:
                pass
    return 0.0


def build_qbo_sales_receipt(receipt, item_map):
    lines = []
    raw_lines = get_receipt_lines(receipt)

    for line in raw_lines:
        loyverse_item_id = get_line_loyverse_item_id(line)
        mapped = item_map["by_id"].get(loyverse_item_id)

        if not mapped:
            fallback_name = normalize_name(get_line_name(line))
            mapped = item_map["by_name"].get(fallback_name)

        if not mapped:
            raise Exception(f"No mapped QBO item for Loyverse item ID: {loyverse_item_id} ({get_line_name(line)})")

        qty = get_line_qty(line)
        unit_price = get_line_price(line)
        amount = qty * unit_price

        lines.append({
            "Amount": amount,
            "DetailType": "SalesItemLineDetail",
            "Description": get_line_name(line),
            "SalesItemLineDetail": {
                "ItemRef": {"value": str(mapped["qbo_item_id"])},
                "Qty": qty,
                "UnitPrice": unit_price
            }
        })

    if not lines:
        raise Exception("Receipt has no usable lines.")

    payload = {
        "DocNumber": f"LOY-{get_receipt_number(receipt)}",
        "Line": lines,
    }

    txn_date = get_receipt_date(receipt)
    if txn_date:
        payload["TxnDate"] = txn_date

    return payload


def main():
    init_db()

    loy = LoyverseClient()
    qbo = QBOClient()

    item_map = load_item_map()
    if not item_map:
        raise Exception("No item mappings found. Run the item matcher/sync first.")

    receipts_raw = loy.get_receipts()
    receipts = receipts_raw.get("receipts", []) if isinstance(receipts_raw, dict) else []

    print(f"Environment: {settings.QBO_ENVIRONMENT}")
    print(f"Loyverse receipts found: {len(receipts)}")
    print(f"Mapped items available: {len(item_map['by_id'])}")

    created = 0
    skipped = 0
    failed = 0

    for receipt in receipts:
        receipt_id = get_receipt_id(receipt)
        receipt_number = get_receipt_number(receipt)
        total_amount = get_receipt_total(receipt)

        try:
            if receipt_already_synced(receipt_id):
                print(f"SKIPPED (already synced in DB): receipt {receipt_number}")
                skipped += 1
                continue

            doc_number = f"LOY-{receipt_number}"
            existing = qbo.get_sales_receipt_by_doc_number(doc_number)
            existing_rows = existing.get("QueryResponse", {}).get("SalesReceipt", [])

            if existing_rows:
                qbo_id = existing_rows[0].get("Id")
                save_receipt_map(receipt_id, qbo_id, receipt_number, total_amount)
                print(f"SKIPPED (already exists in QBO): receipt {receipt_number} -> QBO ID {qbo_id}")
                skipped += 1
                continue

            payload = build_qbo_sales_receipt(receipt, item_map)
            result = qbo.create_sales_receipt(payload)
            qbo_sales_receipt = result.get("SalesReceipt", {})

            save_receipt_map(
                receipt_id,
                qbo_sales_receipt.get("Id"),
                receipt_number,
                total_amount
            )

            print(f"CREATED: receipt {receipt_number} -> QBO SalesReceipt ID {qbo_sales_receipt.get('Id')}")
            created += 1

        except Exception as e:
            print(f"FAILED: receipt {receipt_number} -> {e}")
            failed += 1

    print(f"\nDone. Created: {created}, Skipped: {skipped}, Failed: {failed}")


if __name__ == "__main__":
    main()