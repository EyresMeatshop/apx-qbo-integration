from datetime import datetime
import os

from inventory_reconcile_report import generate_inventory_report
from email_report import send_email_with_attachment
import sync_receipts_loyverse_to_qbo


def main():
    started = datetime.now().isoformat(timespec="seconds")
    print(f"Nightly sync started at {started}")

    print("\nStep 1: Sync receipts Loyverse -> QBO")
    sync_receipts_loyverse_to_qbo.main()

    print(
        "\nStep 2: Stock consistency check — QBO is source of truth vs Loyverse (mapped items)"
    )
    report_path, batch_id, mismatch_count, mapped_pairs = generate_inventory_report()
    print(f"Report file: {report_path}")
    print(f"Mapped Loyverse↔QBO pairs in database: {mapped_pairs}")
    if mapped_pairs == 0:
        print(
            "WARNING: No rows in item_map for this environment — reconciliation compares nothing. "
            "Run sync_items_loyverse_to_qbo.py on Render to populate item_map."
        )
    if mismatch_count == 0:
        print("No stock inconsistencies found for mapped items (Loyverse variant qty vs QBO QtyOnHand).")
    else:
        print(
            f"Found {mismatch_count} stock mismatch(es): Loyverse qty differs from QBO. "
            "Review the CSV / web UI; approving 'Update Loyverse' aligns Loyverse to QBO."
        )

    base_url = os.getenv("APP_BASE_URL", "").rstrip("/")
    review_url = f"{base_url}/login?next=/review/{batch_id}"

    print("\nStep 3: Email inventory reconciliation report")
    try:
        send_email_with_attachment(
            subject=f"Nightly Inventory Reconciliation Report - {batch_id}",
            body=(
                "Attached is the nightly inventory reconciliation report.\n"
                "Quantities are compared with QuickBooks Online (QBO) as the source of truth vs Loyverse.\n\n"
                f"Review and approve decisions here:\n{review_url}\n\n"
                "You will be required to log in and confirm your password again when saving or applying changes."
            ),
            attachment_path=report_path,
        )
        print("Report emailed successfully.")
    except Exception as e:
        print(f"WARNING: Failed to email report: {e}")
        print(f"Report is still available locally at: {report_path}")

    finished = datetime.now().isoformat(timespec="seconds")
    print(f"\nNightly sync finished at {finished}")


if __name__ == "__main__":
    main()