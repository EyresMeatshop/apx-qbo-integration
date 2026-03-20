import os
from config import settings

print("Removing DB at:", settings.DB_PATH)

if os.path.exists(settings.DB_PATH):
    os.remove(settings.DB_PATH)
    print("Deleted existing DB.")
else:
    print("DB file did not exist.")