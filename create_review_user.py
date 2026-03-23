from werkzeug.security import generate_password_hash
from approval_store import init_approval_tables, get_user_by_username, create_user

def main():
    init_approval_tables()

    username = input("Enter username: ").strip()
    password = input("Enter password: ").strip()

    if not username or not password:
        print("Username and password are required.")
        return

    existing = get_user_by_username(username)
    if existing:
        print(f"User '{username}' already exists.")
        return

    password_hash = generate_password_hash(password, method="pbkdf2:sha256")
    create_user(username, password_hash)
    print(f"User '{username}' created successfully.")

if __name__ == "__main__":
    main()