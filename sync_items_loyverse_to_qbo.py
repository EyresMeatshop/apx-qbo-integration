from datetime import datetime

from loyverse_client import LoyverseClient
from qbo_client import QBOClient
from database import get_conn, init_db
from config import settings


def normalize(text):
    text = (text or "").strip().lower()
    text = text.replace("&", "and")
    # normalize separators and whitespace
    for ch in ["-", "_", "/", "\\"]:
        text = text.replace(ch, " ")
    text = " ".join(text.split())
    return text

def normalize_sku(text):
    return (text or "").strip().upper()

def _extract_duplicate_name_id(error_text: str) -> str | None:
    # Example snippet from QBO: "The name supplied already exists. : Id=120"
    if not error_text:
        return None
    marker = "Id="
    idx = error_text.find(marker)
    if idx < 0:
        return None
    tail = error_text[idx + len(marker):].strip()
    digits = ""
    for ch in tail:
        if ch.isdigit():
            digits += ch
        else:
            break
    return digits or None

def _choose_canonical_qbo_item(items: list[dict]) -> dict:
    """
    Prefer:
    - Active items
    - Inventory-tracked items (TrackQtyOnHand True)
    - Lowest numeric Id as stable tie-break
    """
    def score(item: dict):
        active = bool(item.get("Active", True))
        track = bool(item.get("TrackQtyOnHand", False))
        item_id = str(item.get("Id", "")).strip()
        numeric = int(item_id) if item_id.isdigit() else 10**18
        return (1 if active else 0, 1 if track else 0, -numeric)

    return sorted(items, key=score, reverse=True)[0]


def get_price(loy_item):
    for key in ["price", "default_price", "sell_price"]:
        value = loy_item.get(key)
        if value not in (None, ""):
            try:
                return float(value)
            except Exception:
                pass
    return 0.0


def build_qbo_item(loy_item, income_account_id):
    name = loy_item.get("item_name") or loy_item.get("name") or "Unnamed Item"
    sku = loy_item.get("sku") or ""
    price = get_price(loy_item)

    payload = {
        "Name": name[:100],
        "Type": "NonInventory",
        "IncomeAccountRef": {"value": str(income_account_id)},
        "UnitPrice": price,
        "TrackQtyOnHand": False,
    }

    if sku:
        payload["Sku"] = sku

    return payload


def save_mapping(loy_item, qbo_item, source="loyverse_to_qbo", match_method="name"):
    conn = get_conn()
    cur = conn.cursor()

    loy_id = loy_item.get("id")
    loy_name = loy_item.get("item_name") or loy_item.get("name") or ""
    sku = loy_item.get("sku") or ""

    qbo_id = qbo_item.get("Id")
    qbo_name = qbo_item.get("Name") or ""

    # remove any prior mapping for same loyverse item in same environment
    cur.execute("""
        DELETE FROM item_map
        WHERE loyverse_item_id = ?
          AND qbo_environment = ?
    """, (loy_id, settings.QBO_ENVIRONMENT))

    cur.execute("""
        INSERT INTO item_map
        (loyverse_item_id, loyverse_variant_id, qbo_item_id, sku, loyverse_name, qbo_name,
         qbo_environment, match_method, last_source, last_synced_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        loy_id,
        None,
        qbo_id,
        sku,
        loy_name,
        qbo_name,
        settings.QBO_ENVIRONMENT,
        match_method,
        source,
        datetime.utcnow().isoformat()
    ))

    conn.commit()
    conn.close()


def main():
    init_db()

    loy = LoyverseClient()
    qbo = QBOClient()

    income_accounts = qbo.get_first_income_account()
    if not income_accounts:
        raise Exception("No Income account found in QBO sandbox.")

    income_account_id = income_accounts[0]["Id"]
    print("Using Income Account ID:", income_account_id)

    loy_items_raw = loy.get_items()
    loy_items = loy_items_raw.get("items", []) if isinstance(loy_items_raw, dict) else []

    qbo_items = qbo.get_all_items()

    qbo_by_name = {}
    qbo_name_dupes = set()
    qbo_name_groups = {}
    qbo_by_sku = {}
    qbo_sku_dupes = set()
    qbo_sku_groups = {}

    for item in qbo_items:
        name_key = normalize(item.get("Name"))
        if name_key:
            if name_key in qbo_by_name:
                qbo_name_dupes.add(name_key)
            else:
                qbo_by_name[name_key] = item
            qbo_name_groups.setdefault(name_key, []).append(item)

        sku_key = normalize_sku(item.get("Sku"))
        if sku_key:
            if sku_key in qbo_by_sku:
                qbo_sku_dupes.add(sku_key)
            else:
                qbo_by_sku[sku_key] = item
            qbo_sku_groups.setdefault(sku_key, []).append(item)

    print(f"Found {len(loy_items)} Loyverse items")
    print(f"Found {len(qbo_items)} QBO items")
    if qbo_name_dupes:
        print(f"WARNING: Duplicate QBO item Names detected (normalized): {len(qbo_name_dupes)}")
    if qbo_sku_dupes:
        print(f"WARNING: Duplicate QBO item SKUs detected (normalized): {len(qbo_sku_dupes)}")

    created = 0
    skipped = 0
    failed = 0
    ambiguous = 0

    for loy_item in loy_items:
        loy_name = loy_item.get("item_name") or loy_item.get("name") or ""
        loy_sku_key = normalize_sku(loy_item.get("sku"))
        loy_name_key = normalize(loy_name)

        # Strict anti-duplicate rules:
        # - If SKU exists but QBO has multiple items for that SKU, do not create or map automatically.
        # - If Name matches but QBO has multiple items for that Name, do not create or map automatically.
        if loy_sku_key and loy_sku_key in qbo_sku_dupes:
            # If we can still disambiguate (e.g., only one Active), choose canonical.
            candidates = qbo_sku_groups.get(loy_sku_key, [])
            chosen = _choose_canonical_qbo_item(candidates) if candidates else None
            if chosen:
                save_mapping(
                    loy_item,
                    chosen,
                    source="resolved_duplicate_sku_canonical",
                    match_method="sku"
                )
                print(
                    f"RESOLVED (duplicate SKU canonical): {loy_name} | SKU={loy_sku_key} "
                    f"-> QBO ID {chosen.get('Id')}"
                )
                skipped += 1
                continue

            print(f"AMBIGUOUS (duplicate SKU in QBO): {loy_name} | SKU={loy_sku_key}")
            ambiguous += 1
            continue

        if loy_name_key and loy_name_key in qbo_name_dupes:
            # Try to resolve using SKU if present by picking the candidate whose SKU matches.
            candidates = qbo_name_groups.get(loy_name_key, [])
            if loy_sku_key:
                for cand in candidates:
                    if normalize_sku(cand.get("Sku")) == loy_sku_key:
                        save_mapping(
                            loy_item,
                            cand,
                            source="resolved_duplicate_name_by_sku",
                            match_method="sku"
                        )
                        print(
                            f"RESOLVED (duplicate Name by SKU): {loy_name} | SKU={loy_sku_key} "
                            f"-> QBO ID {cand.get('Id')}"
                        )
                        skipped += 1
                        break
                else:
                    # fallback canonical
                    if candidates:
                        chosen = _choose_canonical_qbo_item(candidates)
                        save_mapping(
                            loy_item,
                            chosen,
                            source="resolved_duplicate_name_canonical",
                            match_method="name"
                        )
                        print(
                            f"RESOLVED (duplicate Name canonical): {loy_name} "
                            f"-> QBO ID {chosen.get('Id')}"
                        )
                        skipped += 1
                    else:
                        print(f"AMBIGUOUS (duplicate Name in QBO): {loy_name}")
                        ambiguous += 1
                continue

            if candidates:
                chosen = _choose_canonical_qbo_item(candidates)
                save_mapping(
                    loy_item,
                    chosen,
                    source="resolved_duplicate_name_canonical",
                    match_method="name"
                )
                print(
                    f"RESOLVED (duplicate Name canonical): {loy_name} "
                    f"-> QBO ID {chosen.get('Id')}"
                )
                skipped += 1
                continue

            print(f"AMBIGUOUS (duplicate Name in QBO): {loy_name}")
            ambiguous += 1
            continue

        existing_qbo = None
        match_method = None

        if loy_sku_key:
            existing_qbo = qbo_by_sku.get(loy_sku_key)
            if existing_qbo:
                match_method = "sku"

        if not existing_qbo and loy_name_key:
            existing_qbo = qbo_by_name.get(loy_name_key)
            if existing_qbo:
                match_method = "name"

        try:
            if existing_qbo:
                save_mapping(
                    loy_item,
                    existing_qbo,
                    source="matched_existing_qbo",
                    match_method=match_method or "unknown"
                )
                print(
                    f"SKIPPED (already exists): {loy_name} -> QBO ID {existing_qbo.get('Id')} "
                    f"(match={match_method})"
                )
                skipped += 1
                continue

            payload = build_qbo_item(loy_item, income_account_id)
            try:
                result = qbo.create_item(payload)
                qbo_item = result.get("Item", {})
            except Exception as e:
                # If QBO reports Duplicate Name Exists, recover by mapping to the existing Id.
                msg = str(e)
                detail = ""
                try:
                    resp = getattr(e, "response", None)
                    if resp is not None and getattr(resp, "text", None):
                        detail = resp.text
                except Exception:
                    detail = ""

                combined = (msg + "\n" + detail).strip()
                if (
                    "Duplicate Name Exists Error" in combined
                    or '"code":"6240"' in combined
                    or "code=6240" in combined
                ):
                    existing_id = _extract_duplicate_name_id(combined)
                    if existing_id:
                        fetched = qbo.get_item_by_id(existing_id)
                        rows = fetched.get("QueryResponse", {}).get("Item", [])
                        if isinstance(rows, dict):
                            rows = [rows]
                        if rows:
                            qbo_item = rows[0]
                            save_mapping(
                                loy_item,
                                qbo_item,
                                source="qbo_duplicate_name_recovered",
                                match_method="name"
                            )
                            print(f"RECOVERED (duplicate name): {loy_name} -> QBO ID {existing_id}")
                            skipped += 1
                            continue
                raise

            save_mapping(
                loy_item,
                qbo_item,
                source="created_in_qbo",
                match_method="name"
            )

            print(f"CREATED: {payload['Name']} -> QBO ID {qbo_item.get('Id')}")
            created += 1

        except Exception as e:
            print(f"FAILED: {loy_name} -> {e}")
            failed += 1

    print(f"\nDone. Created: {created}, Skipped: {skipped}, Ambiguous: {ambiguous}, Failed: {failed}")


if __name__ == "__main__":
    main()