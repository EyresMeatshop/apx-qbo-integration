import requests
from config import settings

url = f"https://sandbox-quickbooks.api.intuit.com/v3/company/{settings.QBO_REALM_ID}/companyinfo/{settings.QBO_REALM_ID}"

headers = {
    "Authorization": f"Bearer {settings.QBO_ACCESS_TOKEN}",
    "Accept": "application/json"
}

r = requests.get(url, headers=headers)

print("Status:", r.status_code)
print(r.json())