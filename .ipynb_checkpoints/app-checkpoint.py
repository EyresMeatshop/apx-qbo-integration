from flask import Flask, request, redirect, jsonify, session, url_for, render_template_string
import os
import secrets
import requests
import base64

from werkzeug.security import generate_password_hash, check_password_hash

from approval_store import (
    init_approval_tables,
    get_batch,
    get_batch_items,
    save_batch_decisions,
    get_user_by_username,
    create_user,
    mark_batch_status,
    get_approved_items,
    mark_item_applied,
    log_audit,
    get_audit_log,
)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", secrets.token_urlsafe(32))
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = False  # set True on production HTTPS host


@app.after_request
def apply_security_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response


@app.before_request
def setup_tables():
    init_approval_tables()


def current_user():
    return session.get("review_user")


def require_login():
    return bool(current_user())


def generate_csrf_token():
    token = secrets.token_urlsafe(24)
    session["csrf_token"] = token
    return token


def validate_csrf_token(token: str) -> bool:
    expected = session.get("csrf_token")
    return bool(expected) and bool(token) and secrets.compare_digest(expected, token)


def confirm_same_password(password: str) -> bool:
    username = current_user()
    if not username:
        return False

    user = get_user_by_username(username)
    if not user:
        return False

    return check_password_hash(user["password_hash"], password)


@app.route("/")
def home():
    return "APX Omnipoint QBO Integration Running"


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/seed-review-user")
def seed_review_user():
    username = os.getenv("REVIEW_USERNAME", "").strip()
    password = os.getenv("REVIEW_PASSWORD", "").strip()

    if not username or not password:
        return "Missing REVIEW_USERNAME or REVIEW_PASSWORD in environment.", 400

    existing = get_user_by_username(username)
    if existing:
        return f"Review user '{username}' already exists."

    password_hash = generate_password_hash(password, method="pbkdf2:sha256")
    create_user(username, password_hash)
    return f"Review user '{username}' created successfully."


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    next_url = request.args.get("next", "/")

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        next_url = request.form.get("next", "/")
        csrf_token = request.form.get("csrf_token", "")

        if not validate_csrf_token(csrf_token):
            return "Invalid CSRF token.", 403

        user = get_user_by_username(username)
        if user and check_password_hash(user["password_hash"], password):
            session["review_user"] = username
            log_audit(
                batch_id="LOGIN",
                username=username,
                action_type="login_success",
                details=f"User logged in. next={next_url}",
            )
            return redirect(next_url)

        error = "Invalid username or password."

    csrf_token = generate_csrf_token()
    return render_template_string("""
        <h2>Reconciliation Login</h2>
        {% if error %}<p style="color:red;">{{ error }}</p>{% endif %}
        <form method="post">
            <input type="hidden" name="next" value="{{ next_url }}">
            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
            <label>Username</label><br>
            <input type="text" name="username" required><br><br>
            <label>Password</label><br>
            <input type="password" name="password" required><br><br>
            <button type="submit">Login</button>
        </form>
    """, error=error, next_url=next_url, csrf_token=csrf_token)


@app.route("/review/<batch_id>", methods=["GET"])
def review_batch(batch_id):
    if not require_login():
        return redirect(url_for("login", next=f"/review/{batch_id}"))

    batch = get_batch(batch_id)
    if not batch:
        return "Batch not found.", 404

    items = get_batch_items(batch_id)
    audit_rows = get_audit_log(batch_id)
    csrf_token = generate_csrf_token()

    return render_template_string("""
        <h2>Reconciliation Review - {{ batch_id }}</h2>
        <p>Logged in as: <b>{{ username }}</b> | <a href="{{ url_for('logout') }}">Logout</a></p>
        <p>Batch status: <b>{{ batch["status"] }}</b></p>

        <form method="post" action="{{ url_for('save_review', batch_id=batch_id) }}">
            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
            <table border="1" cellpadding="6" cellspacing="0">
                <tr>
                    <th>Item</th>
                    <th>Loyverse Qty</th>
                    <th>QBO Qty</th>
                    <th>Difference</th>
                    <th>Suggested</th>
                    <th>Approved Action</th>
                    <th>Saved By</th>
                    <th>Saved At</th>
                    <th>Status</th>
                </tr>
                {% for row in items %}
                <tr>
                    <td>{{ row["item_name"] }}</td>
                    <td>{{ row["loyverse_qty"] }}</td>
                    <td>{{ row["qbo_qty"] }}</td>
                    <td>{{ row["difference"] }}</td>
                    <td>{{ row["suggested_action"] }}</td>
                    <td>
                        <select name="action_{{ row['id'] }}">
                            <option value="" {% if not row["approved_action"] %}selected{% endif %}></option>
                            <option value="IGNORE" {% if row["approved_action"] == "IGNORE" %}selected{% endif %}>Leave as is</option>
                            <option value="QBO" {% if row["approved_action"] == "QBO" %}selected{% endif %}>Update QBO</option>
                            <option value="LOYVERSE" {% if row["approved_action"] == "LOYVERSE" %}selected{% endif %}>Update Loyverse</option>
                        </select>
                    </td>
                    <td>{{ row["saved_by"] or "" }}</td>
                    <td>{{ row["saved_at"] or "" }}</td>
                    <td>{{ row["status"] }}</td>
                </tr>
                {% endfor %}
            </table>

            <br>
            <label>Confirm your password to save decisions</label><br>
            <input type="password" name="confirm_password" required><br><br>
            <button type="submit">Save Decisions</button>
        </form>

        <br><hr><br>

        <form method="post" action="{{ url_for('apply_review', batch_id=batch_id) }}">
            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
            <label>Confirm your password to apply approved changes</label><br>
            <input type="password" name="confirm_password" required><br><br>
            <button type="submit">Apply Approved Changes</button>
        </form>

        <br><hr><br>

        <h3>Audit Trail</h3>
        <table border="1" cellpadding="6" cellspacing="0">
            <tr>
                <th>When</th>
                <th>User</th>
                <th>Action</th>
                <th>Old</th>
                <th>New</th>
                <th>Details</th>
            </tr>
            {% for row in audit_rows %}
            <tr>
                <td>{{ row["created_at"] }}</td>
                <td>{{ row["username"] }}</td>
                <td>{{ row["action_type"] }}</td>
                <td>{{ row["old_value"] or "" }}</td>
                <td>{{ row["new_value"] or "" }}</td>
                <td>{{ row["details"] or "" }}</td>
            </tr>
            {% endfor %}
        </table>
    """,
    batch_id=batch_id,
    batch=batch,
    items=items,
    audit_rows=audit_rows,
    username=current_user(),
    csrf_token=csrf_token)


@app.route("/review/<batch_id>/save", methods=["POST"])
def save_review(batch_id):
    if not require_login():
        return redirect(url_for("login", next=f"/review/{batch_id}"))

    csrf_token = request.form.get("csrf_token", "")
    if not validate_csrf_token(csrf_token):
        return "Invalid CSRF token.", 403

    password = request.form.get("confirm_password", "")
    if not confirm_same_password(password):
        return "Password confirmation failed. Decisions not saved.", 403

    username = current_user()
    items = get_batch_items(batch_id)
    decisions = {}

    for row in items:
        action = request.form.get(f"action_{row['id']}", "").strip()
        decisions[row["id"]] = action

    save_batch_decisions(batch_id, decisions, username)
    return redirect(url_for("review_batch", batch_id=batch_id))


@app.route("/review/<batch_id>/apply", methods=["POST"])
def apply_review(batch_id):
    if not require_login():
        return redirect(url_for("login", next=f"/review/{batch_id}"))

    csrf_token = request.form.get("csrf_token", "")
    if not validate_csrf_token(csrf_token):
        return "Invalid CSRF token.", 403

    password = request.form.get("confirm_password", "")
    if not confirm_same_password(password):
        return "Password confirmation failed. Apply denied.", 403

    username = current_user()
    approved_rows = get_approved_items(batch_id)

    for row in approved_rows:
        action = row["approved_action"]

        if action == "IGNORE":
            mark_item_applied(row["id"], username, status="ignored")
            log_audit(
                batch_id=batch_id,
                reconcile_item_id=row["id"],
                username=username,
                action_type="apply_ignore",
                new_value="IGNORE",
                details="Reviewer chose to leave item as is",
            )
            continue

        if action == "QBO":
            # Placeholder for actual QBO correction logic
            mark_item_applied(row["id"], username, status="applied")
            log_audit(
                batch_id=batch_id,
                reconcile_item_id=row["id"],
                username=username,
                action_type="apply_qbo",
                new_value="QBO",
                details="Approved to update QBO from reconciliation decision",
            )
            continue

        if action == "LOYVERSE":
            # Placeholder for actual Loyverse correction logic
            mark_item_applied(row["id"], username, status="applied")
            log_audit(
                batch_id=batch_id,
                reconcile_item_id=row["id"],
                username=username,
                action_type="apply_loyverse",
                new_value="LOYVERSE",
                details="Approved to update Loyverse from reconciliation decision",
            )

    mark_batch_status(batch_id, "applied", username)
    log_audit(
        batch_id=batch_id,
        username=username,
        action_type="apply_batch",
        details="Reviewer applied approved actions for batch",
    )
    return redirect(url_for("review_batch", batch_id=batch_id))


@app.route("/connect")
def connect():
    state = secrets.token_urlsafe(16)
    auth_url = (
        "https://appcenter.intuit.com/connect/oauth2"
        f"?client_id={os.getenv('QBO_CLIENT_ID', '')}"
        f"&redirect_uri={os.getenv('QBO_REDIRECT_URI', '')}"
        "&response_type=code"
        "&scope=com.intuit.quickbooks.accounting"
        f"&state={state}"
    )
    return redirect(auth_url)


@app.route("/qbo-callback")
def callback():
    error = request.args.get("error")
    if error:
        return f"OAuth error: {error}", 400

    auth_code = request.args.get("code")
    realm_id = request.args.get("realmId")

    if not auth_code:
        return "Missing auth code.", 400

    client_id = os.getenv("QBO_CLIENT_ID", "")
    client_secret = os.getenv("QBO_CLIENT_SECRET", "")
    redirect_uri = os.getenv("QBO_REDIRECT_URI", "")

    token_url = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
    basic = f"{client_id}:{client_secret}"
    basic_b64 = base64.b64encode(basic.encode("utf-8")).decode("utf-8")

    headers = {
        "Accept": "application/json",
        "Authorization": f"Basic {basic_b64}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": redirect_uri,
    }

    r = requests.post(token_url, headers=headers, data=data, timeout=30)

    if not r.ok:
        return f"Token exchange failed: {r.status_code}", 400

    token_json = r.json()
    access_token = token_json.get("access_token", "")
    refresh_token = token_json.get("refresh_token", "")

    print("QBO OAuth connected successfully")
    print(f"Realm ID received: {bool(realm_id)}")
    print(f"Access token received: {bool(access_token)}")
    print(f"Refresh token received: {bool(refresh_token)}")

    return redirect("/success")


@app.route("/success")
def success():
    return "QuickBooks Connected Successfully"


@app.route("/privacy")
def privacy():
    return """
    <h1>Privacy Policy</h1>
    <p>This application is used internally to sync data between Loyverse POS and QuickBooks Online.</p>
    <p>No customer data is shared with unauthorized third parties.</p>
    <p>Data is used strictly for accounting, reporting, and synchronization purposes.</p>
    """


@app.route("/terms")
def terms():
    return """
    <h1>Terms of Service</h1>
    <p>This application is a private internal tool used by Eyre's Meat Shop.</p>
    <p>Use of this system is restricted to authorized personnel only.</p>
    <p>The application is intended only for approved business synchronization and accounting workflows.</p>
    """


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)