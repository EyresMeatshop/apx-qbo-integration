from database import init_db
from approval_store import init_approval_tables
from sync_event_store import (
    init_sync_event_tables,
    get_pending_events,
    mark_event_processing,
    mark_event_processed,
    mark_event_failed,
    mark_event_ignored,
    create_sync_link,
)
from loyverse_to_qbo_sync import collect_loyverse_events, process_loyverse_event
from qbo_to_loyverse_sync import (
    collect_qbo_events,
    collect_qbo_item_qty_events,
    process_qbo_event,
    process_qbo_item_event,
    process_qbo_item_qty_event,
)


def process_pending_batch(limit: int = 500) -> int:
    pending = get_pending_events(limit=limit)
    print(f"Pending events found: {len(pending)}")

    processed_count = 0

    for event in pending:
        event_id = event["id"]
        source_system = event["source_system"]
        event_type = event["event_type"]

        try:
            mark_event_processing(event_id)

            if source_system == "LOYVERSE":
                result = process_loyverse_event(event)

            elif source_system == "QBO" and event_type == "invoice":
                result = process_qbo_event(event)

            elif source_system == "QBO" and event_type == "invoice_item":
                result = process_qbo_item_event(event)

            elif source_system == "QBO" and event_type == "item_qty":
                result = process_qbo_item_qty_event(event)

            else:
                mark_event_ignored(event_id, f"Unknown event route: {source_system}/{event_type}")
                processed_count += 1
                continue

            if result.get("status") == "processed":
                if result.get("target_system") and result.get("target_reference"):
                    create_sync_link(
                        source_system=event["source_system"],
                        source_event_id=event["source_event_id"],
                        target_system=result["target_system"],
                        target_reference=result["target_reference"],
                    )
                mark_event_processed(event_id)

            elif result.get("status") == "ignored":
                mark_event_ignored(event_id, result.get("reason", "Ignored by processor"))

            else:
                mark_event_failed(event_id, result.get("error", "Unknown processing failure"))

            processed_count += 1

        except Exception as e:
            mark_event_failed(event_id, str(e))
            processed_count += 1

    return processed_count


def main():
    init_db()
    init_approval_tables()
    init_sync_event_tables()

    print("Step 1: Collect new Loyverse events")
    try:
        loyverse_new = collect_loyverse_events()
        print(f"Loyverse events collected: {loyverse_new}")
    except Exception as e:
        print(f"WARNING: Loyverse event collection failed: {e}")

    print("Step 2: Collect new QBO events")
    try:
        qbo_new = collect_qbo_events()
        qbo_qty_new = collect_qbo_item_qty_events()
        print(f"QBO events collected: invoices={qbo_new}, item_qty={qbo_qty_new}")
    except Exception as e:
        print(f"WARNING: QBO event collection failed: {e}")

    print("Step 3: Process pending events")
    total_processed = 0
    max_passes = 10

    for i in range(max_passes):
        print(f"\nProcessing pass {i + 1}/{max_passes}")
        processed = process_pending_batch(limit=500)
        total_processed += processed

        if processed == 0:
            print("No more pending events to process.")
            break

    print(f"\nLive sync runner finished. Total processed this run: {total_processed}")


if __name__ == "__main__":
    main()