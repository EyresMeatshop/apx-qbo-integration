"""
Create review UI users from REVIEW_USERS_JSON in the environment.

Loads config (`.env` / `.env.production`) the same way as the app.

Example (set in shell or Render, then run once):

  REVIEW_USERS_JSON=[{"username":"a@b.com","password":"secret1"},{"username":"c@d.com","password":"secret2"}]
  python seed_review_users_json.py
"""
import json
import os

from werkzeug.security import generate_password_hash

import config  # noqa: F401 — loads .env / .env.production via config.py

from approval_store import init_approval_tables, get_user_by_username, create_user


def main():
    init_approval_tables()
    raw = (os.getenv("REVIEW_USERS_JSON") or "").strip()
    if not raw:
        print("Set REVIEW_USERS_JSON to a JSON array of {\"username\",\"password\"} objects.")
        return

    users = json.loads(raw)
    if not isinstance(users, list):
        print("REVIEW_USERS_JSON must be a JSON array.")
        return

    for entry in users:
        if not isinstance(entry, dict):
            continue
        username = (entry.get("username") or "").strip()
        password = entry.get("password") or ""
        if not username or not password:
            continue
        if get_user_by_username(username):
            print(f"Skip (exists): {username}")
            continue
        h = generate_password_hash(password, method="pbkdf2:sha256")
        create_user(username, h)
        print(f"Created: {username}")

    print("Done.")


if __name__ == "__main__":
    main()
