from werkzeug.security import generate_password_hash
from database import get_conn
from approval_store import init_approval_tables, get_user_by_username

def main():
    init_approval_tables()

    username = input("Enter username: ").strip()
    new_password = input("Enter new password: ").strip()

    if not username or not new_password:
        print("Username and password are required.")
        return

    user = get_user_by_username(username)
    if not user:
        print(f"User '{username}' not found.")
        return

    password_hash = generate_password_hash(new_password, method="pbkdf2:sha256")

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE review_users SET password_hash = ? WHERE username = ?",
            (password_hash, username),
        )
        conn.commit()
    finally:
        conn.close()

    print(f"Password updated for '{username}'.")

if __name__ == "__main__":
    main()