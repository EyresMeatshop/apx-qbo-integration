"""
Exchange a QuickBooks OAuth authorization code for access + refresh tokens.

Usage:
  1. In browser, open your app: https://YOUR-HOST/connect
  2. After Intuit redirects, copy `code` and `realmId` from the URL bar.
  3. Run (replace with real values from the callback URL):
       python exchange_qbo_auth_code.py "LONG_CODE_FROM_URL" "REALM_ID_NUMBER"

Requires in .env / .env.production:
  QBO_CLIENT_ID, QBO_CLIENT_SECRET, QBO_REDIRECT_URI (must match Intuit app + redirect URL)

If you see error invalid_grant / Invalid authorization code:
  - redirect_uri here MUST be identical (character-for-character) to the one used when you
    opened /connect. If you authorized on Render but run this script with localhost in
    QBO_REDIRECT_URI, exchange will fail. Copy QBO_REDIRECT_URI from Render into
    .env.production for this run, or run the script on a machine whose env matches Render.
  - Authorization codes are single-use and short-lived. If /qbo-callback already ran
    (you saw /success), the code is consumed — start a new /connect flow and do not reuse
    the old code.
"""
import base64
import json
import sys
from urllib.parse import unquote

import requests

from config import settings


def _print_token_error(status: int, body: str) -> None:
    print("\n--- Token exchange error ---")
    print(f"HTTP {status}")
    try:
        err = json.loads(body)
        print(json.dumps(err, indent=2))
        desc = (err.get("error_description") or err.get("error") or "").lower()
        if "invalid_grant" in desc or "authorization code" in desc:
            print(
                "\nLikely causes:\n"
                "  • Code already used (browser hit /qbo-callback first) or expired — use /connect again.\n"
                "  • QBO_REDIRECT_URI in this shell does not match /connect (e.g. Render vs localhost).\n"
                "  • Wrong QBO_CLIENT_ID / QBO_CLIENT_SECRET for the app that issued the code.\n"
                "  • Code copied wrong — use the raw value from the URL, or try without extra quotes/spaces."
            )
    except json.JSONDecodeError:
        print(body or "(empty body)")


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)

    code = unquote(sys.argv[1].strip())
    realm_id = sys.argv[2].strip()

    if len(code) < 10:
        print("The authorization code looks too short — paste the full `code` from the callback URL.")
        sys.exit(1)

    if not all([settings.QBO_CLIENT_ID, settings.QBO_CLIENT_SECRET, settings.QBO_REDIRECT_URI]):
        print("Missing QBO_CLIENT_ID, QBO_CLIENT_SECRET, or QBO_REDIRECT_URI in environment.")
        sys.exit(1)

    print("Using QBO_REDIRECT_URI:", repr(settings.QBO_REDIRECT_URI))
    cid = settings.QBO_CLIENT_ID
    print("Using QBO_CLIENT_ID:", f"{cid[:6]}...{cid[-4:]}" if len(cid) > 12 else "(short)")
    print("(Must match the app URL used for /connect and your Intuit Developer redirect URI.)\n")

    token_url = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
    basic = f"{settings.QBO_CLIENT_ID}:{settings.QBO_CLIENT_SECRET}"
    basic_b64 = base64.b64encode(basic.encode("utf-8")).decode("utf-8")

    headers = {
        "Accept": "application/json",
        "Authorization": f"Basic {basic_b64}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.QBO_REDIRECT_URI,
    }

    r = requests.post(token_url, headers=headers, data=data, timeout=60)
    if not r.ok:
        _print_token_error(r.status_code, r.text or "")
        sys.exit(1)

    token_json = r.json()
    access = token_json.get("access_token", "")
    refresh = token_json.get("refresh_token", "")

    print("\n=== Add these to Render (or .env.production) ===\n")
    print(f"QBO_REALM_ID={realm_id}")
    print(f"QBO_ACCESS_TOKEN={access}")
    print(f"QBO_REFRESH_TOKEN={refresh}")
    print("\n=== JSON (for your records) ===\n")
    print(json.dumps({**token_json, "realm_id_from_you": realm_id}, indent=2))


if __name__ == "__main__":
    main()
