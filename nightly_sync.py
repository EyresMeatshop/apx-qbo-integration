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

    print("\nStep 2: Generate inventory reconciliation report")
    report_path, batch_id = generate_inventory_report()
    print(f"Inventory reconciliation report created: {report_path}")

    base_url = os.getenv("APP_BASE_URL", "").rstrip("/")
    review_url = f"{base_url}/login?next=/review/{batch_id}"

    print("\nStep 3: Email inventory reconciliation report")
    try:
        send_email_with_attachment(
            subject=f"Nightly Inventory Reconciliation Report - {batch_id}",
            body=(
                "Attached is the nightly inventory reconciliation report.\n\n"
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