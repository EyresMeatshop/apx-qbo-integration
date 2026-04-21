import time

import requests
from config import settings


def normalize_loyverse_item_id(item_id: str | None) -> str:
    """Keys in build_item_variant_index are lowercased Loyverse item UUIDs."""
    return str(item_id or "").strip().lower()


class LoyverseClient:
    def __init__(self):
        self.base_url = settings.LOYVERSE_API_BASE
        self.token = settings.LOYVERSE_ACCESS_TOKEN
        self.session = requests.Session()
        self.session.trust_env = False

    def headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _get(self, path, params=None):
        url = f"{self.base_url}{path}"
        last_error = None

        for attempt in range(3):
            try:
                r = self.session.get(url, headers=self.headers(), params=params, timeout=30)
                if not r.ok:
                    body = (r.text or "")[:2000]
                    print(f"LOYVERSE API HTTP {r.status_code} {path}")
                    if body:
                        print(body)
                r.raise_for_status()
                return r.json()
            except requests.exceptions.HTTPError:
                raise
            except requests.exceptions.ConnectionError as e:
                last_error = e
                print(f"LOYVERSE GET retry {attempt + 1}/3 after connection error: {e}")
                time.sleep(2)

        raise last_error
    def get_items(self):
        all_items = []
        cursor = None

        while True:
            params = {"limit": 250}
            if cursor:
                params["cursor"] = cursor

            data = self._get("/items", params=params)

            items = data.get("items", []) if isinstance(data, dict) else []
            all_items.extend(items)

            cursor = data.get("cursor")
            if not cursor:
                break

        return {"items": all_items}

    def get_receipts(self):
        all_receipts = []
        cursor = None

        while True:
            # Max page size reduces how many paginated calls we make (some accounts hit 402 on page 2+).
            params = {"limit": 250}
            if cursor:
                params["cursor"] = cursor

            try:
                data = self._get("/receipts", params=params)
            except requests.exceptions.HTTPError as e:
                resp = getattr(e, "response", None)
                # 402 often means plan/subscription limit (e.g. full receipt export). Use what we have.
                if resp is not None and resp.status_code == 402 and all_receipts:
                    print(
                        "LOYVERSE: HTTP 402 on receipt pagination — stopping with partial list. "
                        "This usually means a Loyverse plan/add-on limit; check Back Office billing or Loyverse support. "
                        f"Fetched so far: {len(all_receipts)} receipt(s)."
                    )
                    break
                raise

            receipts = data.get("receipts", []) if isinstance(data, dict) else []
            all_receipts.extend(receipts)

            cursor = data.get("cursor")
            if not cursor:
                break

        return {"receipts": all_receipts}

    def get_customers(self):
        all_customers = []
        cursor = None

        while True:
            params = {"limit": 250}
            if cursor:
                params["cursor"] = cursor

            data = self._get("/customers", params=params)

            customers = data.get("customers", []) if isinstance(data, dict) else []
            all_customers.extend(customers)

            cursor = data.get("cursor")
            if not cursor:
                break

        return {"customers": all_customers}

    def _post(self, path: str, payload):
        url = f"{self.base_url}{path}"
        last_error = None

        for attempt in range(3):
            try:
                r = self.session.post(url, headers=self.headers(), json=payload, timeout=30)
                if not r.ok:
                    body = (r.text or "")[:2000]
                    print(f"LOYVERSE API HTTP {r.status_code} POST {path}")
                    if body:
                        print(body)
                r.raise_for_status()
                return r.json() if r.text else {}
            except requests.exceptions.ConnectionError as e:
                last_error = e
                print(f"LOYVERSE POST retry {attempt + 1}/3 after connection error: {e}")
                time.sleep(2)

        raise last_error

    def get_variant_stock_totals(self) -> dict[str, float]:
        """
        Sum in_stock per variant across stores from GET /inventory (paginated).

        Keys are lowercased variant UUID strings for case-insensitive lookup.
        If /inventory is unavailable, returns {} and callers fall back to /items fields.
        """
        totals: dict[str, float] = {}
        cursor = None
        try:
            while True:
                params: dict = {"limit": 250}
                if cursor:
                    params["cursor"] = cursor
                try:
                    data = self._get("/inventory", params=params)
                except requests.exceptions.HTTPError as e:
                    resp = getattr(e, "response", None)
                    code = resp.status_code if resp is not None else None
                    if code == 404:
                        print("LOYVERSE: GET /inventory returned 404 — using stock fields on /items only.")
                    else:
                        print(f"LOYVERSE: GET /inventory failed ({code}); using /items stock fields only.")
                    return {}

                rows = (
                    data.get("inventory_levels")
                    or data.get("InventoryLevels")
                    or data.get("inventory")
                    or []
                )
                if isinstance(rows, dict):
                    rows = [rows]
                if not isinstance(rows, list):
                    rows = []

                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    vid = (
                        row.get("variant_id")
                        or row.get("variantId")
                        or row.get("id")
                    )
                    if not vid:
                        continue
                    vk = str(vid).strip().lower()
                    if "in_stock" in row:
                        qty = row.get("in_stock")
                    elif "quantity" in row:
                        qty = row.get("quantity")
                    else:
                        qty = row.get("stock")
                    try:
                        q = float(qty if qty is not None else 0)
                    except Exception:
                        q = 0.0
                    totals[vk] = totals.get(vk, 0.0) + q

                cursor = data.get("cursor") if isinstance(data, dict) else None
                if not cursor:
                    break
        except requests.exceptions.HTTPError:
            return {}
        except Exception as e:
            print(f"LOYVERSE: inventory totals skipped: {e}")
            return {}

        return totals

    def build_item_variant_index(self) -> dict[str, dict]:
        """
        Index keyed by **lowercased** Loyverse item id:

          { "<item_id_lower>": {"variant_id": "...", "in_stock": float} }

        Stock priority:
        1) GET /inventory sums per variant (when available)
        2) Embedded in_stock on item / first variant from GET /items

        Items are always indexed when an id exists (no longer skipped when variant_id was missing).
        """
        stock_by_variant = self.get_variant_stock_totals()

        data = self.get_items()
        items = data.get("items", []) if isinstance(data, dict) else []

        idx: dict[str, dict] = {}

        for item in items:
            item_id = item.get("id") or item.get("item_id")
            if not item_id:
                continue

            item_key = str(item_id).strip().lower()

            variants = item.get("variants")
            if isinstance(variants, dict):
                variants = [variants]
            if not isinstance(variants, list):
                variants = []

            chosen_variant = variants[0] if variants else {}
            variant_id = (
                chosen_variant.get("variant_id")
                or chosen_variant.get("id")
                or chosen_variant.get("variantId")
                or item.get("variant_id")
            )
            # Single-SKU items: API may only expose the item id; use it as variant for inventory.
            if not variant_id:
                variant_id = item_id

            vid_key = str(variant_id).strip().lower()

            in_stock_val = 0.0
            if vid_key in stock_by_variant:
                in_stock_val = stock_by_variant[vid_key]
            else:
                in_stock = None
                if "in_stock" in chosen_variant:
                    in_stock = chosen_variant.get("in_stock")
                elif "in_stock" in item:
                    in_stock = item.get("in_stock")
                else:
                    in_stock = chosen_variant.get("stock") or item.get("stock")

                try:
                    in_stock_val = float(in_stock if in_stock is not None else 0)
                except Exception:
                    in_stock_val = 0.0

            idx[item_key] = {
                "variant_id": str(variant_id),
                "in_stock": in_stock_val,
            }

        return idx

    def update_inventory_levels(self, inventory_levels: list[dict]):
        """
        Update inventory levels for variants.

        Expected input:
        [
          {"variant_id": "<id>", "in_stock": 10},
          ...
        ]
        """
        if not inventory_levels:
            return {}

        # Many Loyverse accounts require store_id for inventory updates.
        store_id = (getattr(settings, "LOYVERSE_STORE_ID", "") or "").strip()
        if store_id:
            enriched = []
            for row in inventory_levels:
                if not isinstance(row, dict):
                    continue
                if "store_id" not in row and "storeId" not in row:
                    row = dict(row)
                    row["store_id"] = store_id
                enriched.append(row)
            inventory_levels = enriched

        # Loyverse API uses POST /inventory for bulk inventory updates.
        # Some accounts accept a top-level list; others require an object wrapper.
        try:
            return self._post("/inventory", inventory_levels)
        except Exception:
            return self._post("/inventory", {"inventory_levels": inventory_levels})