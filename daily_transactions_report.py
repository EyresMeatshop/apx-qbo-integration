"""
Daily sync activity report (CSV).

Pulls rows from sync_events for "today" in Atlantic Standard Time (AST, UTC-4),
which matches scheduled_nightly.py's notion of local day for Barbados.

This is a reporting summary of what the integration observed/processed, based on
queued sync_events + lightweight parsing of raw_payload when helpful.
"""

from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from config import settings
from database import get_conn, init_db
from sync_event_store import init_sync_event_tables


REPORTS_DIR = Path("reports")
REPORTS_DIR.mkdir(exist_ok=True)


def _ast_now() -> datetime:
    return datetime.utcnow() + timedelta(hours=-4)


def _parse_dt_loose(value: str) -> datetime | None:
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _fmt_ast(dt: datetime | None) -> str:
    if not dt:
        return ""
    # Display in AST by shifting from naive UTC timestamps we store.
    try:
        ast_dt = dt + timedelta(hours=-4)
        return ast_dt.strftime("%Y-%m-%d %H:%M:%S AST")
    except Exception:
        return ""


@dataclass(frozen=True)
class ReportWindow:
    start_ast: datetime
    end_ast: datetime

    @property
    def label_day(self) -> str:
        return self.start_ast.date().isoformat()


def _report_window_from_env(now_ast: datetime) -> ReportWindow:
    """
    Default: from local midnight AST through "now" AST.

    Optional env overrides:
      - DAILY_REPORT_DAY=YYYY-MM-DD  (interpreted as AST calendar date)
    """
    day_raw = (os.getenv("DAILY_REPORT_DAY") or "").strip()
    if day_raw:
        day = datetime.strptime(day_raw, "%Y-%m-%d").date()
        start_ast = datetime(day.year, day.month, day.day, 0, 0, 0)
        end_ast = now_ast
        if end_ast < start_ast:
            end_ast = start_ast
        return ReportWindow(start_ast=start_ast, end_ast=end_ast)

    start_ast = datetime(now_ast.year, now_ast.month, now_ast.day, 0, 0, 0)
    return ReportWindow(start_ast=start_ast, end_ast=now_ast)


def _window_to_utc_bounds(win: ReportWindow) -> tuple[datetime, datetime]:
    """
    DB timestamps are stored as UTC ISO strings without tz (via datetime.utcnow()).
    Convert AST window endpoints to UTC naive datetimes for comparison.
    """
    start_utc = win.start_ast + timedelta(hours=4)
    end_utc = win.end_ast + timedelta(hours=4)
    return start_utc, end_utc


def _category_and_summary(row: dict) -> tuple[str, str]:
    src = (row.get("source_system") or "").strip().upper()
    et = (row.get("event_type") or "").strip().lower()
    st = (row.get("status") or "").strip().lower()

    ref = (row.get("source_reference") or "").strip()
    seid = (row.get("source_event_id") or "").strip()

    item_name = (row.get("item_name") or "").strip()
    qty = row.get("quantity")

    raw = row.get("raw_payload") or ""

    if src == "LOYVERSE" and et == "sale_receipt":
        cat = "Loyverse sale → QBO"
        summary = f"Loyverse receipt queued for SalesReceipt sync ({ref or seid})"
        if st == "processed":
            summary += " — processed"
        elif st == "failed":
            summary += f" — FAILED ({(row.get('error_message') or '')[:180]})"
        return cat, summary

    if src == "QBO" and et == "invoice":
        cat = "QBO invoice detected → enqueue line items"
        doc = ref
        summary = f"Invoice detected ({doc or seid})"
        try:
            payload = json.loads(raw or "{}")
            doc2 = (payload.get("DocNumber") or "").strip()
            txn = (payload.get("TxnDate") or "").strip()
            if doc2:
                summary = f"Invoice {doc2}" + (f" ({txn})" if txn else "")
        except Exception:
            pass
        return cat, summary

    if src == "QBO" and et == "invoice_item":
        cat = "QBO invoice line → Loyverse stock"
        summary = f"Invoice line → Loyverse adjustment"
        if item_name:
            summary += f" — {item_name}"
        if qty is not None:
            summary += f" (qty_delta={qty})"
        return cat, summary

    if src == "QBO" and et == "item_qty":
        cat = "QBO inventory qty → Loyverse stock"
        summary = "QBO QtyOnHand change → set Loyverse stock"
        try:
            payload = json.loads(raw or "{}")
            qbo_item_id = str(payload.get("qbo_item_id") or "").strip()
            qoh = payload.get("qty_on_hand")
            if qbo_item_id:
                summary += f" — qbo_item_id={qbo_item_id}"
            if qoh is not None:
                summary += f", target_qty={qoh}"
        except Exception:
            pass
        return cat, summary

    cat = f"{src or 'UNKNOWN'}:{et or 'unknown'}"
    summary = ref or seid or ""
    return cat, summary


def generate_daily_transactions_report() -> tuple[str, str]:
    """
    Returns (csv_path, day_label).
    """
    init_db()
    init_sync_event_tables()

    now_ast = _ast_now()
    win = _report_window_from_env(now_ast)
    start_utc, end_utc = _window_to_utc_bounds(win)

    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_path = REPORTS_DIR / f"daily_transactions_{settings.QBO_ENVIRONMENT}_{win.label_day}_{stamp}.csv"

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT *
            FROM sync_events
            WHERE created_at >= ?
              AND created_at <= ?
            ORDER BY created_at ASC, id ASC
            """,
            (
                start_utc.replace(microsecond=0).isoformat(timespec="seconds"),
                end_utc.replace(microsecond=0).isoformat(timespec="seconds"),
            ),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "day_ast",
                "created_at_utc",
                "created_at_ast",
                "source_system",
                "event_type",
                "status",
                "category",
                "summary",
                "source_event_id",
                "source_reference",
                "item_name",
                "quantity",
                "error_message",
            ],
        )
        writer.writeheader()

        for r in rows:
            created = _parse_dt_loose(str(r.get("created_at") or ""))
            cat, summary = _category_and_summary(dict(r))
            writer.writerow(
                {
                    "day_ast": win.label_day,
                    "created_at_utc": str(r.get("created_at") or ""),
                    "created_at_ast": _fmt_ast(created),
                    "source_system": r.get("source_system") or "",
                    "event_type": r.get("event_type") or "",
                    "status": r.get("status") or "",
                    "category": cat,
                    "summary": summary,
                    "source_event_id": r.get("source_event_id") or "",
                    "source_reference": r.get("source_reference") or "",
                    "item_name": r.get("item_name") or "",
                    "quantity": r.get("quantity") if r.get("quantity") is not None else "",
                    "error_message": r.get("error_message") or "",
                }
            )

    return str(out_path), win.label_day

