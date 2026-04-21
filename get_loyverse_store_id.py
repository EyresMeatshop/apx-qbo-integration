import os
import sys

import requests


def main() -> int:
    token = (os.getenv("LOYVERSE_ACCESS_TOKEN") or "").strip()
    base = (os.getenv("LOYVERSE_API_BASE") or "https://api.loyverse.com/v1.0").strip().rstrip("/")

    if not token:
        print("Missing LOYVERSE_ACCESS_TOKEN in environment.", file=sys.stderr)
        print("Set it, then re-run this script.", file=sys.stderr)
        return 2

    url = f"{base}/stores"
    try:
        r = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=30,
        )
    except Exception as e:
        print(f"Request failed: {e}", file=sys.stderr)
        return 3

    if not r.ok:
        body = (r.text or "")[:2000]
        print(f"HTTP {r.status_code} from {url}", file=sys.stderr)
        if body:
            print(body, file=sys.stderr)
        return 4

    data = r.json()
    stores = data.get("stores", []) if isinstance(data, dict) else []
    if not stores:
        print("No stores returned. Response keys:", list(data.keys()) if isinstance(data, dict) else type(data))
        return 0

    print("Loyverse stores found:")
    for s in stores:
        if not isinstance(s, dict):
            continue
        sid = (s.get("id") or "").strip()
        name = (s.get("name") or s.get("title") or "").strip()
        active = s.get("is_active")
        print(f"- name={name or '(no name)'} | id={sid} | is_active={active}")

    print("\nSet LOYVERSE_STORE_ID to the id of the store you want.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

