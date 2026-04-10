import json

from sync_event_store import init_sync_event_tables, get_pending_events, mark_event_processing, mark_event_processed
from qbo_to_loyverse_sync import process_qbo_item_qty_event


def test_process_qbo_item_qty_event_missing_loyverse_item_id():
    init_sync_event_tables()

    event = {
        "id": 0,
        "source_system": "QBO",
        "event_type": "item_qty",
        "source_event_id": "1:10",
        "raw_payload": json.dumps({"qbo_item_id": "1", "qty_on_hand": 10}),
    }
    result = process_qbo_item_qty_event(event)
    assert result["status"] == "failed"


if __name__ == "__main__":
    test_process_qbo_item_qty_event_missing_loyverse_item_id()
    print("OK")

