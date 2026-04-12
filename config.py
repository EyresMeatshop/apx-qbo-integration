import os
from pathlib import Path

from dotenv import load_dotenv

# Resolve env files next to this file so tokens load even when cwd is not the project root.
_ROOT = Path(__file__).resolve().parent
_explicit = (os.getenv("APP_ENV_FILE") or "").strip()
if _explicit:
    _env_path = Path(_explicit) if os.path.isabs(_explicit) else _ROOT / _explicit
    load_dotenv(_env_path)
else:
    # Default: .env then .env.production (later file overrides) — common local layout.
    load_dotenv(_ROOT / ".env")
    load_dotenv(_ROOT / ".env.production", override=True)
load_dotenv()  # optional: cwd .env overrides

_APP_ENV = os.getenv("APP_ENV", "development").strip().lower()


class Settings:
    APP_ENV = _APP_ENV

    LOYVERSE_ACCESS_TOKEN = (os.getenv("LOYVERSE_ACCESS_TOKEN", "") or "").strip()
    LOYVERSE_API_BASE = (os.getenv("LOYVERSE_API_BASE", "https://api.loyverse.com/v1.0") or "").strip().rstrip("/")

    QBO_CLIENT_ID = os.getenv("QBO_CLIENT_ID", "")
    QBO_CLIENT_SECRET = os.getenv("QBO_CLIENT_SECRET", "")
    QBO_REDIRECT_URI = os.getenv("QBO_REDIRECT_URI", "http://localhost:5000/qbo-callback")
    QBO_ENVIRONMENT = os.getenv("QBO_ENVIRONMENT", "sandbox")

    QBO_REALM_ID = os.getenv("QBO_REALM_ID", "")
    QBO_ACCESS_TOKEN = os.getenv("QBO_ACCESS_TOKEN", "")
    QBO_REFRESH_TOKEN = os.getenv("QBO_REFRESH_TOKEN", "")

    SECRET_KEY = os.getenv("SECRET_KEY", "change_me_please")

    # Prefer DATABASE_URL on hosted platforms (Render/Heroku). Falls back to local SQLite path.
    DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

    DB_PATH = os.getenv("DB_PATH", "data/sync.db")

    # Production safety default: do not dry-run unless explicitly requested.
    _dry = os.getenv("DRY_RUN", "").strip().lower()
    if not _dry:
        DRY_RUN = False if _APP_ENV == "production" else True
    else:
        DRY_RUN = _dry in ("1", "true", "yes", "y", "on")

settings = Settings()