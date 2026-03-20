from loyverse_client import LoyverseClient
from qbo_client import QBOClient

def normalize(text):
    return (text or "").strip().lower()

loy = LoyverseClient()
qbo = QBOClient()

loy_items_raw = loy.get_items()
qbo_items_raw = qbo.get_items()

loy_items = loy_items_raw.get("items", []) if isinstance(loy_items_raw, dict) else []
qbo_items = qbo_items_raw.get("QueryResponse", {}).get("Item", [])

print(f"Loyverse items: {len(loy_items)}")
print(f"QBO items: {len(qbo_items)}")

qbo_by_name = {normalize(item.get("Name")): item for item in qbo_items}

for item in loy_items[:50]:
    loy_name = item.get("item_name") or item.get("name") or ""
    match = qbo_by_name.get(normalize(loy_name))
    if match:
        print(f"MATCH: Loyverse '{loy_name}' -> QBO '{match.get('Name')}'")
    else:
        print(f"NO MATCH: Loyverse '{loy_name}'")