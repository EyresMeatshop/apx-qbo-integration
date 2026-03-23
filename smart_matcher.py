import re
from typing import Dict, Optional


MANUAL_NAME_MAP: Dict[str, str] = {
    # Existing manual mappings
    "wholesale pork lean meat": "Pork lean meat",
    "filleted snapper": "snapper",
    "burger 1 4 pounder": "Burger 1/4 pounder",
    "chicken drumsticks": "Drumsticks",
    "pork riblets": "Pork Riblets 22lbs",
    "goat stew": "Goat",
    "lamb shoulder chops": "Lamb Shoulder",
    "lamb leg chops": "Lamb leg",
    "20 lb chicken leg quarters": "20 LB Chicken Leg Special - copy",
    "10 lb chicken leg quarters": "10 LB Chicken Leg Special",

    # Remaining matches based on QBO names / sales descriptions
    "50 lb chicken leg": "chicken  leg",
    "spareribs imported": "Spareribs Imported - Prime Cut",
    "local turkey": "Turkey - Local",
    "10 lb pork leg chops": "pork Leg Special",
    "boneless pig head": "Pig Head - Boneless",
    "chicken wings": "Chicken Wings (local)",
}


def normalize_name(name: str) -> str:
    s = (name or "").strip().lower()

    # Normalize fractions first
    s = s.replace("1/4", "1 4")

    # Normalize punctuation to spaces
    s = re.sub(r"[-:/(),.]", " ", s)

    # Standardize common units
    s = re.sub(r"\blbs\b", "lb", s)
    s = re.sub(r"\bpounds\b", "lb", s)

    # Remove filler words that often vary between systems
    s = re.sub(r"\bspecial\b", " ", s)
    s = re.sub(r"\bcopy\b", " ", s)

    # Collapse extra spaces
    s = re.sub(r"\s+", " ", s).strip()
    return s


def build_qbo_index(qbo_items: list[dict]) -> Dict[str, dict]:
    index: Dict[str, dict] = {}

    for item in qbo_items:
        # Index by Product/Service Name
        name = item.get("Name", "")
        norm_name = normalize_name(name)
        if norm_name and norm_name not in index:
            index[norm_name] = item

        # Index by Sales Description, fallback to other description fields if present
        sales_desc = (
            item.get("SalesDesc", "")
            or item.get("Description", "")
            or item.get("PurchaseDesc", "")
        )
        norm_desc = normalize_name(sales_desc)
        if norm_desc and norm_desc not in index:
            index[norm_desc] = item

    return index


def find_qbo_match(loyverse_name: str, qbo_items: list[dict]) -> Optional[dict]:
    qbo_index = build_qbo_index(qbo_items)
    loy_norm = normalize_name(loyverse_name)

    # 1. Manual override first
    manual_qbo_name = MANUAL_NAME_MAP.get(loy_norm)
    if manual_qbo_name:
        manual_norm = normalize_name(manual_qbo_name)
        if manual_norm in qbo_index:
            return qbo_index[manual_norm]

    # 2. Exact normalized match against QBO Name or SalesDesc
    if loy_norm in qbo_index:
        return qbo_index[loy_norm]

    # 3. Lightweight semantic fallback
    fallback = loy_norm
    fallback = fallback.replace("local ", "")
    fallback = fallback.replace(" boneless", "")
    fallback = fallback.replace(" chops", "")
    fallback = fallback.replace(" quarters", "")
    fallback = fallback.replace(" imported", "")
    fallback = fallback.strip()

    if fallback in qbo_index:
        return qbo_index[fallback]

    return None