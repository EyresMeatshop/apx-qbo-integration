import base64
from datetime import datetime
import requests
from config import settings
from database import init_db, load_qbo_tokens, upsert_qbo_tokens
from token_store import get_active_env_file, update_env_file


class QBOClient:
    def __init__(self):
        self.realm_id = settings.QBO_REALM_ID
        self.access_token = settings.QBO_ACCESS_TOKEN
        self.refresh_token = settings.QBO_REFRESH_TOKEN
        self.client_id = settings.QBO_CLIENT_ID
        self.client_secret = settings.QBO_CLIENT_SECRET

        self.base_url = (
            "https://sandbox-quickbooks.api.intuit.com"
            if settings.QBO_ENVIRONMENT == "sandbox"
            else "https://quickbooks.api.intuit.com"
        )

        self.session = requests.Session()
        self.session.trust_env = False

        # Cloud-friendly token persistence: prefer DB tokens when present.
        init_db()
        db_tokens = load_qbo_tokens(self.realm_id)
        if db_tokens:
            self.access_token = db_tokens.get("access_token") or self.access_token
            self.refresh_token = db_tokens.get("refresh_token") or self.refresh_token

    def headers(self):
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def refresh_access_token(self):
        token_url = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"

        basic = f"{self.client_id}:{self.client_secret}"
        basic_b64 = base64.b64encode(basic.encode("utf-8")).decode("utf-8")

        headers = {
            "Accept": "application/json",
            "Authorization": f"Basic {basic_b64}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        data = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
        }

        r = self.session.post(token_url, headers=headers, data=data, timeout=30)
        r.raise_for_status()

        token_json = r.json()

        self.access_token = token_json["access_token"]
        self.refresh_token = token_json["refresh_token"]

        now = datetime.utcnow().isoformat(timespec="seconds")
        upsert_qbo_tokens(self.realm_id, self.access_token, self.refresh_token, now)

        try:
            env_file = get_active_env_file()
            update_env_file(
                env_file,
                {
                    "QBO_ACCESS_TOKEN": self.access_token,
                    "QBO_REFRESH_TOKEN": self.refresh_token,
                },
            )
        except OSError:
            # Typical on cloud hosts with read-only disks; DB persistence still applies.
            pass

        print("\nQBO tokens refreshed and persisted (database).")

    def _get(self, url, params=None):
        r = self.session.get(url, headers=self.headers(), params=params, timeout=30)
        if r.status_code == 401:
            print("Access token expired. Refreshing token...")
            self.refresh_access_token()
            r = self.session.get(url, headers=self.headers(), params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def _post(self, url, payload):
        r = self.session.post(url, headers=self.headers(), json=payload, timeout=30)
        if r.status_code == 401:
            print("Access token expired. Refreshing token...")
            self.refresh_access_token()
            r = self.session.post(url, headers=self.headers(), json=payload, timeout=30)
    
        if not r.ok:
            print("POST FAILED")
            print("URL:", url)
            print("STATUS:", r.status_code)
            print("RESPONSE:", r.text)
            print("PAYLOAD:", payload)
    
        r.raise_for_status()
        return r.json()

    def query(self, sql):
        url = f"{self.base_url}/v3/company/{self.realm_id}/query"
        return self._get(url, params={"query": sql})

    def get_company_info(self):
        url = f"{self.base_url}/v3/company/{self.realm_id}/companyinfo/{self.realm_id}"
        return self._get(url)

    def get_items(self):
        return self.query("SELECT * FROM Item MAXRESULTS 1000")

    def get_all_items(self, page_size: int = 1000, max_pages: int = 50) -> list[dict]:
        """
        Fetch all QBO Items using paging.

        QBO query results are paged; MAXRESULTS alone does not guarantee you get every item.
        """
        all_items: list[dict] = []
        start_position = 1

        for _ in range(max_pages):
            sql = f"SELECT * FROM Item STARTPOSITION {start_position} MAXRESULTS {page_size}"
            data = self.query(sql)
            items = data.get("QueryResponse", {}).get("Item", [])
            if isinstance(items, dict):
                items = [items]
            if not items:
                break

            all_items.extend(items)
            if len(items) < page_size:
                break

            start_position += page_size

        return all_items

    def get_item_by_id(self, item_id: str):
        safe = str(item_id).replace("'", "\\'")
        return self.query(f"SELECT * FROM Item WHERE Id = '{safe}' MAXRESULTS 1")

    def get_sales_receipt_by_doc_number(self, doc_number):
        safe = doc_number.replace("'", "\\'")
        return self.query(f"SELECT * FROM SalesReceipt WHERE DocNumber = '{safe}' MAXRESULTS 10")

    def create_item(self, payload):
        url = f"{self.base_url}/v3/company/{self.realm_id}/item"
        return self._post(url, payload)

    def update_item(self, payload):
        """
        QBO updates use the same endpoint as create, but require Id + SyncToken.
        Callers should set 'sparse': True when sending partial updates.
        """
        url = f"{self.base_url}/v3/company/{self.realm_id}/item"
        return self._post(url, payload)

    def create_sales_receipt(self, payload):
        url = f"{self.base_url}/v3/company/{self.realm_id}/salesreceipt"
        return self._post(url, payload)

    def get_first_income_account(self):
        data = self.query("SELECT * FROM Account WHERE AccountType = 'Income' MAXRESULTS 10")
        return data.get("QueryResponse", {}).get("Account", [])

    def get_first_cogs_account(self):
        data = self.query("SELECT * FROM Account WHERE AccountType = 'Cost of Goods Sold' MAXRESULTS 10")
        return data.get("QueryResponse", {}).get("Account", [])

    def get_first_inventory_asset_account(self):
        # QBO typically stores inventory assets under Other Current Asset.
        data = self.query("SELECT * FROM Account WHERE AccountType = 'Other Current Asset' MAXRESULTS 50")
        accounts = data.get("QueryResponse", {}).get("Account", [])
        # Prefer subtype Inventory if present
        for a in accounts:
            if (a.get("AccountSubType") or "").lower() in ("inventory", "inventoryasset"):
                return [a]
        return accounts[:1]