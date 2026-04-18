from flask import Flask, request, redirect, jsonify, session, url_for, render_template_string, flash
import os
import secrets
import requests
import base64
from datetime import datetime
from urllib.parse import quote

from html import escape
from werkzeug.security import generate_password_hash, check_password_hash

from config import settings
from database import init_db, upsert_qbo_tokens

from approval_store import (
    init_approval_tables,
    get_batch,
    get_batch_items,
    get_reconcile_item,
    complete_reconcile_item,
    finalize_batch_if_all_items_done,
    get_user_by_username,
    create_user,
    log_audit,
    get_audit_log,
)
from reconcile_actions import fix_loyverse_to_match_qbo, fix_qbo_to_match_loyverse

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", secrets.token_urlsafe(32))
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
_secure_flag = os.getenv("SESSION_COOKIE_SECURE", "").strip().lower()
if _secure_flag:
    app.config["SESSION_COOKIE_SECURE"] = _secure_flag in ("1", "true", "yes", "y", "on")
else:
    app.config["SESSION_COOKIE_SECURE"] = settings.APP_ENV == "production"


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


@app.route("/")
def home():
    return "Eyre's Meat Chop — QBO integration running"


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/seed-review-user")
def seed_review_user():
    """
    One-time: create review login users from environment.
    Set either REVIEW_USERNAME + REVIEW_PASSWORD, or REVIEW_USERS_JSON (array of {username, password}).
    If REVIEW_SEED_TOKEN is set, call /seed-review-user?token=YOUR_TOKEN
    """
    import json

    expected = os.getenv("REVIEW_SEED_TOKEN", "").strip()
    if expected and request.args.get("token", "") != expected:
        return "Forbidden: invalid or missing token.", 403

    created = []
    skipped = []

    bulk = (os.getenv("REVIEW_USERS_JSON") or "").strip()
    if bulk:
        try:
            users = json.loads(bulk)
        except json.JSONDecodeError as e:
            return f"Invalid REVIEW_USERS_JSON: {e}", 400
        if not isinstance(users, list):
            return "REVIEW_USERS_JSON must be a JSON array.", 400
        for entry in users:
            if not isinstance(entry, dict):
                continue
            username = (entry.get("username") or "").strip()
            password = entry.get("password") or ""
            if not username or not password:
                continue
            existing = get_user_by_username(username)
            if existing:
                skipped.append(username)
                continue
            password_hash = generate_password_hash(password, method="pbkdf2:sha256")
            create_user(username, password_hash)
            created.append(username)
        return (
            f"Created: {created or 'none'}. Already existed (skipped): {skipped or 'none'}."
        )

    username = os.getenv("REVIEW_USERNAME", "").strip()
    password = os.getenv("REVIEW_PASSWORD", "").strip()

    if not username or not password:
        return (
            "Missing REVIEW_USERNAME/REVIEW_PASSWORD or REVIEW_USERS_JSON in environment.",
            400,
        )

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
        <!DOCTYPE html>
        <html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Login — Eyre's Meat Chop</title>
        <style>
            body { font-family: system-ui, sans-serif; background: #f6f7f9; margin: 0; padding: 2rem; }
            .box { max-width: 400px; margin: 0 auto; background: #fff; padding: 1.5rem; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,.1); }
            h2 { margin-top: 0; }
            label { display: block; margin-top: 0.75rem; font-weight: 500; }
            input[type=text], input[type=password] { width: 100%; box-sizing: border-box; padding: 0.5rem; margin-top: 0.25rem; }
            button { margin-top: 1rem; padding: 0.5rem 1rem; background: #1a73e8; color: #fff; border: none; border-radius: 4px; cursor: pointer; }
            .err { color: #c5221f; }
        </style></head><body>
        <div class="box">
        <h2>Eyre's Meat Chop — reconciliation</h2>
        <p style="color:#5f6368;font-size:0.9rem;">Sign in to review stock discrepancies.</p>
        {% if error %}<p class="err">{{ error }}</p>{% endif %}
        <form method="post">
            <input type="hidden" name="next" value="{{ next_url }}">
            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
            <label>Email (username)</label>
            <input type="text" name="username" autocomplete="username" required>
            <label>Password</label>
            <input type="password" name="password" autocomplete="current-password" required>
            <button type="submit">Login</button>
        </form>
        </div>
        </body></html>
    """, error=error, next_url=next_url, csrf_token=csrf_token)


def _review_post_guard(batch_id: str):
    if not require_login():
        return redirect(url_for("login", next=f"/review/{batch_id}"))
    csrf_token = request.form.get("csrf_token", "")
    if not validate_csrf_token(csrf_token):
        flash("Invalid CSRF token. Refresh the page and try again.", "error")
        return redirect(url_for("review_batch", batch_id=batch_id))
    return None


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
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>Stock reconciliation — {{ batch_id }}</title>
            <style>
                body { font-family: system-ui, sans-serif; margin: 0; background: #f6f7f9; color: #1a1a1a; }
                .wrap { max-width: 1200px; margin: 0 auto; padding: 1.25rem; }
                h2 { margin-top: 0; }
                .flash { padding: 0.65rem 1rem; border-radius: 6px; margin-bottom: 1rem; }
                .flash.success { background: #e6f4ea; color: #137333; border: 1px solid #34a853; }
                .flash.error { background: #fce8e6; color: #c5221f; border: 1px solid #ea4335; }
                table.data { width: 100%; border-collapse: collapse; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
                table.data th, table.data td { border: 1px solid #dadce0; padding: 0.5rem 0.65rem; text-align: left; vertical-align: top; }
                table.data th { background: #f1f3f4; font-weight: 600; }
                .actions { display: flex; flex-wrap: wrap; gap: 0.4rem; align-items: center; }
                .actions form { display: inline; margin: 0; }
                button.btn-loy { background: #1a73e8; color: #fff; border: none; padding: 0.45rem 0.75rem; border-radius: 4px; cursor: pointer; font-size: 0.875rem; }
                button.btn-qbo { background: #188038; color: #fff; border: none; padding: 0.45rem 0.75rem; border-radius: 4px; cursor: pointer; font-size: 0.875rem; }
                button.btn-skip { background: #fff; color: #5f6368; border: 1px solid #dadce0; padding: 0.45rem 0.75rem; border-radius: 4px; cursor: pointer; font-size: 0.875rem; }
                button:disabled { opacity: 0.5; cursor: not-allowed; }
                .meta { color: #5f6368; font-size: 0.9rem; }
                .pill { display: inline-block; padding: 0.15rem 0.5rem; border-radius: 999px; font-size: 0.8rem; }
                .pill.pending { background: #fef7e0; color: #b06000; }
                .pill.done { background: #e8f0fe; color: #1967d2; }
            </style>
        </head>
        <body>
        <div class="wrap">
        <h2>Eyre's Meat Chop — stock reconciliation</h2>
        <p class="meta">Batch <code>{{ batch_id }}</code> &nbsp;|&nbsp; Logged in as <b>{{ username }}</b>
           &nbsp;|&nbsp; <a href="{{ url_for('logout') }}">Logout</a></p>
        <p>QBO quantity is the default source of truth in reports. Use the buttons to align one system or skip.</p>

        {% with messages = get_flashed_messages(with_categories=true) %}
          {% if messages %}
            {% for category, message in messages %}
              <div class="flash {{ category }}">{{ message }}</div>
            {% endfor %}
          {% endif %}
        {% endwith %}

        <table class="data">
            <thead>
                <tr>
                    <th>Item</th>
                    <th>Loyverse</th>
                    <th>QBO</th>
                    <th>Diff (L−Q)</th>
                    <th>Actions</th>
                    <th>Outcome</th>
                </tr>
            </thead>
            <tbody>
            {% for row in items %}
                <tr>
                    <td><strong>{{ row["item_name"] }}</strong></td>
                    <td>{{ row["loyverse_qty"] }}</td>
                    <td>{{ row["qbo_qty"] }}</td>
                    <td>{{ row["difference"] }}</td>
                    <td>
                        {% if row["status"] == "pending" %}
                        <div class="actions">
                            <form method="post" action="{{ url_for('review_fix_loyverse', batch_id=batch_id, item_id=row['id']) }}"
                                  onsubmit="return confirm('Set Loyverse stock to {{ row['qbo_qty'] }} (match QBO)?');">
                                <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                                <button type="submit" class="btn-loy">Fix Loyverse</button>
                            </form>
                            <form method="post" action="{{ url_for('review_fix_qbo', batch_id=batch_id, item_id=row['id']) }}"
                                  onsubmit="return confirm('Set QBO quantity to {{ row['loyverse_qty'] }} (match Loyverse)?');">
                                <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                                <button type="submit" class="btn-qbo">Fix QBO</button>
                            </form>
                            <form method="post" action="{{ url_for('review_skip_item', batch_id=batch_id, item_id=row['id']) }}"
                                  onsubmit="return confirm('Leave both systems unchanged for this item?');">
                                <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                                <button type="submit" class="btn-skip">Do nothing</button>
                            </form>
                        </div>
                        {% else %}
                        <span class="meta">Processed</span>
                        {% endif %}
                    </td>
                    <td>
                        <span class="pill {{ 'pending' if row['status'] == 'pending' else 'done' }}">{{ row["status"] }}</span>
                        {% if row["approved_action"] %}<br><small class="meta">{{ row["approved_action"] }} — {{ row["applied_by"] or row["saved_by"] or "" }}</small>{% endif %}
                    </td>
                </tr>
            {% else %}
                <tr><td colspan="6">No discrepancies in this batch.</td></tr>
            {% endfor %}
            </tbody>
        </table>

        <h3 style="margin-top:2rem;">Audit trail</h3>
        <table class="data">
            <thead><tr><th>When</th><th>User</th><th>Action</th><th>Details</th></tr></thead>
            <tbody>
            {% for row in audit_rows %}
                <tr>
                    <td>{{ row["created_at"] }}</td>
                    <td>{{ row["username"] }}</td>
                    <td>{{ row["action_type"] }}</td>
                    <td>{{ row["details"] or "" }} {{ row["new_value"] or "" }}</td>
                </tr>
            {% else %}
                <tr><td colspan="4">No audit entries yet.</td></tr>
            {% endfor %}
            </tbody>
        </table>
        </div>
        </body>
        </html>
    """,
        batch_id=batch_id,
        items=items,
        audit_rows=audit_rows,
        username=current_user(),
        csrf_token=csrf_token,
    )


@app.route("/review/<batch_id>/item/<int:item_id>/fix-loyverse", methods=["POST"])
def review_fix_loyverse(batch_id, item_id):
    redir = _review_post_guard(batch_id)
    if redir is not None:
        return redir

    username = current_user()
    row = get_reconcile_item(batch_id, item_id)
    if not row:
        flash("Row not found.", "error")
        return redirect(url_for("review_batch", batch_id=batch_id))
    if row["status"] != "pending":
        flash("This line was already processed.", "error")
        return redirect(url_for("review_batch", batch_id=batch_id))

    try:
        fix_loyverse_to_match_qbo(row)
        complete_reconcile_item(batch_id, item_id, "LOYVERSE", username, "applied")
        log_audit(
            batch_id=batch_id,
            reconcile_item_id=item_id,
            username=username,
            action_type="apply_loyverse",
            new_value="LOYVERSE",
            details="Loyverse stock set to QBO QtyOnHand",
        )
        flash("Loyverse inventory updated to match QuickBooks.", "success")
        finalize_batch_if_all_items_done(batch_id, username)
    except Exception as e:
        flash(str(e), "error")

    return redirect(url_for("review_batch", batch_id=batch_id))


@app.route("/review/<batch_id>/item/<int:item_id>/fix-qbo", methods=["POST"])
def review_fix_qbo(batch_id, item_id):
    redir = _review_post_guard(batch_id)
    if redir is not None:
        return redir

    username = current_user()
    row = get_reconcile_item(batch_id, item_id)
    if not row:
        flash("Row not found.", "error")
        return redirect(url_for("review_batch", batch_id=batch_id))
    if row["status"] != "pending":
        flash("This line was already processed.", "error")
        return redirect(url_for("review_batch", batch_id=batch_id))

    try:
        fix_qbo_to_match_loyverse(row)
        complete_reconcile_item(batch_id, item_id, "QBO", username, "applied")
        log_audit(
            batch_id=batch_id,
            reconcile_item_id=item_id,
            username=username,
            action_type="apply_qbo",
            new_value="QBO",
            details="QBO QtyOnHand set to Loyverse quantity",
        )
        flash("QuickBooks inventory updated to match Loyverse.", "success")
        finalize_batch_if_all_items_done(batch_id, username)
    except Exception as e:
        flash(str(e), "error")

    return redirect(url_for("review_batch", batch_id=batch_id))


@app.route("/review/<batch_id>/item/<int:item_id>/skip", methods=["POST"])
def review_skip_item(batch_id, item_id):
    redir = _review_post_guard(batch_id)
    if redir is not None:
        return redir

    username = current_user()
    row = get_reconcile_item(batch_id, item_id)
    if not row:
        flash("Row not found.", "error")
        return redirect(url_for("review_batch", batch_id=batch_id))
    if row["status"] != "pending":
        flash("This line was already processed.", "error")
        return redirect(url_for("review_batch", batch_id=batch_id))

    complete_reconcile_item(batch_id, item_id, "IGNORE", username, "ignored")
    log_audit(
        batch_id=batch_id,
        reconcile_item_id=item_id,
        username=username,
        action_type="apply_ignore",
        new_value="IGNORE",
        details="No inventory changes (reviewer chose do nothing)",
    )
    flash("No changes made for this item.", "success")
    finalize_batch_if_all_items_done(batch_id, username)
    return redirect(url_for("review_batch", batch_id=batch_id))


@app.route("/connect")
def connect():
    state = secrets.token_urlsafe(16)
    client_id = os.getenv("QBO_CLIENT_ID", "")
    redirect_uri = os.getenv("QBO_REDIRECT_URI", "")
    # Intuit requires redirect_uri in the authorize URL to be percent-encoded.
    auth_url = (
        "https://appcenter.intuit.com/connect/oauth2"
        f"?client_id={quote(client_id, safe='')}"
        f"&redirect_uri={quote(redirect_uri, safe='')}"
        "&response_type=code"
        "&scope=com.intuit.quickbooks.accounting"
        f"&state={quote(state, safe='')}"
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
        return (
            "<h1>QuickBooks connect</h1>"
            "<p>No authorization <code>code</code> was sent to this page. That is normal if you opened "
            "this URL directly.</p>"
            "<p>Start here instead: "
            '<a href="/connect">/connect</a> — sign in with Intuit; you will be sent back here '
            "with a code automatically.</p>"
            "<p>Use the callback path <strong>/qbo-callback</strong> (with a hyphen) in Intuit and in "
            "<code>QBO_REDIRECT_URI</code>.</p>",
            400,
        )

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
        return f"Token exchange failed: {r.status_code} {r.text}", 400

    token_json = r.json()
    access_token = token_json.get("access_token", "")
    refresh_token = token_json.get("refresh_token", "")

    print("QBO OAuth connected successfully")
    print(f"Realm ID received: {bool(realm_id)}")
    print(f"Access token received: {bool(access_token)}")
    print(f"Refresh token received: {bool(refresh_token)}")

    if realm_id and access_token and refresh_token:
        try:
            init_db()
            upsert_qbo_tokens(
                realm_id,
                access_token,
                refresh_token,
                datetime.utcnow().isoformat(timespec="seconds"),
            )
            print("QBO tokens persisted to database.")
        except Exception as e:
            print(f"Could not persist QBO tokens to database: {e}")

    return redirect(url_for("success", realm_id=realm_id or ""))

@app.route("/qbo_callback")
def qbo_callback_legacy():
    """Underscore URL: redirect to canonical /qbo-callback so OAuth ?code=... is preserved."""
    qs = dict(request.args)
    return redirect(url_for("callback", **qs), code=307)


@app.route("/success")
def success():
    realm_id = (request.args.get("realm_id") or "").strip()
    body = "<h1>QuickBooks Connected Successfully</h1>"
    if realm_id:
        safe = escape(realm_id)
        body += (
            f"<p><strong>Realm ID</strong> — set this in Render as <code>QBO_REALM_ID</code> "
            f"(must match for the app to load stored tokens):</p>"
            f"<p><code>{safe}</code></p>"
        )
    body += (
        "<p>If your service uses <code>DATABASE_URL</code>, access and refresh tokens "
        "were saved there. Redeploy or restart workers so they pick up "
        "<code>QBO_REALM_ID</code> if you just added it.</p>"
    )
    return body


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