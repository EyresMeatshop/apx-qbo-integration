"""
Apply inventory reconciliation: Loyverse ↔ QBO using quantities from a reconcile_items row.
"""

from __future__ import annotations

from loyverse_client import LoyverseClient, normalize_loyverse_item_id
from qbo_client import QBOClient


def fix_loyverse_to_match_qbo(
    row,
    *,
    loy: LoyverseClient | None = None,
    loy_variant_index: dict[str, dict] | None = None,
) -> None:
    """Set Loyverse variant stock to QBO QtyOnHand (truth = QBO)."""
    loyverse_item_id = str(row["loyverse_item_id"] or "").strip()
    qbo_qty = float(row["qbo_qty"] or 0)

    if not loyverse_item_id:
        raise ValueError("Missing Loyverse item id on reconciliation row.")

    loy = loy or LoyverseClient()
    idx = loy_variant_index or loy.build_item_variant_index()
    vinfo = idx.get(normalize_loyverse_item_id(loyverse_item_id))
    if not vinfo or not vinfo.get("variant_id"):
        raise ValueError(
            f"No Loyverse variant found for item id {loyverse_item_id}. "
            "Check item still exists in Loyverse."
        )

    ts = vinfo.get("track_stock")
    if ts is False or str(ts).strip().lower() in ("0", "false", "no", "off"):
        raise ValueError(
            "This Loyverse item has stock tracking disabled (track_stock=false). "
            "Enable inventory tracking for this item in Loyverse before updating quantities."
        )

    loy.update_inventory_levels(
        [{"variant_id": str(vinfo["variant_id"]), "in_stock": qbo_qty}]
    )


def fix_qbo_to_match_loyverse(row, *, qbo: QBOClient | None = None) -> None:
    """Set QBO inventory QtyOnHand to Loyverse quantity (truth = Loyverse for this action)."""
    qbo_item_id = str(row["qbo_item_id"] or "").strip()
    loy_qty = float(row["loyverse_qty"] or 0)

    if not qbo_item_id:
        raise ValueError("Missing QBO item id on reconciliation row.")

    qbo = qbo or QBOClient()
    raw = qbo.get_item_by_id(qbo_item_id)
    items = raw.get("QueryResponse", {}).get("Item", [])
    if isinstance(items, dict):
        items = [items]
    if not items:
        raise ValueError("QuickBooks item not found (may be deleted).")

    ent = items[0]
    itype = (ent.get("Type") or "").strip().lower()
    if itype != "inventory":
        raise ValueError(
            f"QBO item “{ent.get('Name', '')}” is type “{ent.get('Type')}”, not Inventory. "
            "On-hand quantity can only be set for Inventory items."
        )

    payload = {
        "Id": ent["Id"],
        "SyncToken": ent["SyncToken"],
        "sparse": True,
        "QtyOnHand": loy_qty,
    }
    qbo.update_item(payload)
