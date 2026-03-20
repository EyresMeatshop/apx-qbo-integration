import requests
from config import settings
import time
import requests


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
                r.raise_for_status()
                return r.json()
            except requests.exceptions.ConnectionError as e:
                last_error = e
                print(f"LOYVERSE GET retry {attempt + 1}/3 after connection error: {e}")
                time.sleep(2)
    
        raise last_error
    def get_items(self):
        all_items = []
        cursor = None

        while True:
            params = {}
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
            params = {}
            if cursor:
                params["cursor"] = cursor

            data = self._get("/receipts", params=params)

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
            params = {}
            if cursor:
                params["cursor"] = cursor

            data = self._get("/customers", params=params)

            customers = data.get("customers", []) if isinstance(data, dict) else []
            all_customers.extend(customers)

            cursor = data.get("cursor")
            if not cursor:
                break

        return {"customers": all_customers}