import os
from dotenv import load_dotenv

ENV_FILE = os.getenv("APP_ENV_FILE", ".env")
load_dotenv(ENV_FILE)

class Settings:
    LOYVERSE_ACCESS_TOKEN = os.getenv("LOYVERSE_ACCESS_TOKEN", "")
    LOYVERSE_API_BASE = os.getenv("LOYVERSE_API_BASE", "https://api.loyverse.com/v1.0")

    QBO_CLIENT_ID = os.getenv("QBO_CLIENT_ID", "")
    QBO_CLIENT_SECRET = os.getenv("QBO_CLIENT_SECRET", "")
    QBO_REDIRECT_URI = os.getenv("QBO_REDIRECT_URI", "http://localhost:5000/qbo-callback")
    QBO_ENVIRONMENT = os.getenv("QBO_ENVIRONMENT", "sandbox")

    QBO_REALM_ID = os.getenv("QBO_REALM_ID", "")
    QBO_ACCESS_TOKEN = os.getenv("QBO_ACCESS_TOKEN", "")
    QBO_REFRESH_TOKEN = os.getenv("QBO_REFRESH_TOKEN", "")

    SECRET_KEY = os.getenv("SECRET_KEY", "change_me_please")
    DB_PATH = os.getenv("DB_PATH", "data/sync.db")
    DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

settings = Settings()