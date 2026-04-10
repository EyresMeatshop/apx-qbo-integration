import csv
from collections import defaultdict
from pathlib import Path

from qbo_client import QBOClient


def normalize_name(text: str) -> str:
    text = (text or "").strip().lower()
    text = text.replace("&", "and")
    for ch in ["-", "_", "/", "\\"]:
        text = text.replace(ch, " ")
    text = " ".join(text.split())
    return text


def normalize_sku(text: str) -> str:
    return (text or "").strip().upper()


def choose_canonical(items: list[dict]) -> dict:
    """
    Prefer:
    - Active items
    - Inventory items (TrackQtyOnHand True) when available
    - Lowest numeric Id as stable tie-break
    """
    def score(item: dict):
        active = bool(item.get("Active", True))
        track = bool(item.get("TrackQtyOnHand", False))
        item_id = str(item.get("Id", "")).strip()
        numeric = int(item_id) if item_id.isdigit() else 10**18
        return (1 if active else 0, 1 if track else 0, -numeric)

    return sorted(items, key=score, reverse=True)[0]


def export_csv(path: Path, rows: list[dict], fieldnames: list[str]):
    path.parent.mkdir(exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def main():
    qbo = QBOClient()
    items = qbo.get_all_items()

    by_name = defaultdict(list)
    by_sku = defaultdict(list)

    for it in items:
        name_key = normalize_name(it.get("Name"))
        sku_key = normalize_sku(it.get("Sku"))
        if name_key:
            by_name[name_key].append(it)
        if sku_key:
            by_sku[sku_key].append(it)

    name_dupes = {k: v for k, v in by_name.items() if len(v) > 1}
    sku_dupes = {k: v for k, v in by_sku.items() if len(v) > 1}

    reports_dir = Path("reports")

    name_rows = []
    for key, group in sorted(name_dupes.items(), key=lambda x: x[0]):
        keep = choose_canonical(group)
        for it in group:
            name_rows.append({
                "dupe_key": key,
                "keep": "YES" if str(it.get("Id")) == str(keep.get("Id")) else "NO",
                "Id": it.get("Id"),
                "Name": it.get("Name"),
                "Sku": it.get("Sku") or "",
                "Active": it.get("Active", True),
                "TrackQtyOnHand": it.get("TrackQtyOnHand", False),
                "QtyOnHand": it.get("QtyOnHand", ""),
                "Type": it.get("Type", ""),
            })

    sku_rows = []
    for key, group in sorted(sku_dupes.items(), key=lambda x: x[0]):
        keep = choose_canonical(group)
        for it in group:
            sku_rows.append({
                "dupe_key": key,
                "keep": "YES" if str(it.get("Id")) == str(keep.get("Id")) else "NO",
                "Id": it.get("Id"),
                "Name": it.get("Name"),
                "Sku": it.get("Sku") or "",
                "Active": it.get("Active", True),
                "TrackQtyOnHand": it.get("TrackQtyOnHand", False),
                "QtyOnHand": it.get("QtyOnHand", ""),
                "Type": it.get("Type", ""),
            })

    export_csv(
        reports_dir / "qbo_item_name_duplicates.csv",
        name_rows,
        ["dupe_key", "keep", "Id", "Name", "Sku", "Active", "TrackQtyOnHand", "QtyOnHand", "Type"],
    )
    export_csv(
        reports_dir / "qbo_item_sku_duplicates.csv",
        sku_rows,
        ["dupe_key", "keep", "Id", "Name", "Sku", "Active", "TrackQtyOnHand", "QtyOnHand", "Type"],
    )

    print(f"QBO items fetched: {len(items)}")
    print(f"Duplicate Names (normalized): {len(name_dupes)} -> reports/qbo_item_name_duplicates.csv")
    print(f"Duplicate SKUs (normalized): {len(sku_dupes)} -> reports/qbo_item_sku_duplicates.csv")


if __name__ == "__main__":
    main()

