import base64
import requests
from config import settings


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

        print("\nNEW TOKENS GENERATED")
        print("Update your .env file with:")
        print(f"QBO_ACCESS_TOKEN={self.access_token}")
        print(f"QBO_REFRESH_TOKEN={self.refresh_token}\n")

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

    def get_sales_receipt_by_doc_number(self, doc_number):
        safe = doc_number.replace("'", "\\'")
        return self.query(f"SELECT * FROM SalesReceipt WHERE DocNumber = '{safe}' MAXRESULTS 10")

    def create_item(self, payload):
        url = f"{self.base_url}/v3/company/{self.realm_id}/item"
        return self._post(url, payload)

    def create_sales_receipt(self, payload):
        url = f"{self.base_url}/v3/company/{self.realm_id}/salesreceipt"
        return self._post(url, payload)

    def get_first_income_account(self):
        data = self.query("SELECT * FROM Account WHERE AccountType = 'Income' MAXRESULTS 10")
        return data.get("QueryResponse", {}).get("Account", [])