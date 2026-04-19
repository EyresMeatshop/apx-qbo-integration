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
    update_user_password,
    log_audit,
    get_audit_log,
)
from reconcile_actions import fix_loyverse_to_match_qbo, fix_qbo_to_match_loyverse
from inventory_reconcile_report import count_item_map_rows

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


def verify_logged_in_password(password: str) -> bool:
    """True if password matches the logged-in review user (for re-authentication)."""
    username = current_user()
    if not username or password is None:
        return False
    user = get_user_by_username(username)
    if not user:
        return False
    return check_password_hash(user["password_hash"], password)


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


_MIN_NEW_PASSWORD_LEN = 10


def _safe_internal_path(raw: str | None) -> str | None:
    """Allow only same-origin paths like /review/... for 'next' links."""
    s = (raw or "").strip()
    if not s or not s.startswith("/") or s.startswith("//"):
        return None
    if " " in s or "\n" in s:
        return None
    if len(s) > 512:
        return None
    return s


@app.route("/change-password", methods=["GET", "POST"])
def change_password():
    if not require_login():
        return redirect(url_for("login", next="/change-password"))

    username = current_user()
    error = ""
    if request.method == "POST":
        back_review = _safe_internal_path(request.form.get("next") or request.args.get("next"))
    else:
        back_review = _safe_internal_path(request.args.get("next"))

    if request.method == "POST":
        csrf_token = request.form.get("csrf_token", "")
        if not validate_csrf_token(csrf_token):
            return "Invalid CSRF token.", 403

        current_pw = request.form.get("current_password", "")
        new_pw = request.form.get("new_password", "")
        confirm_pw = request.form.get("new_password_confirm", "")

        user = get_user_by_username(username)
        if not user or not check_password_hash(user["password_hash"], current_pw):
            error = "Current password is incorrect."
        elif len(new_pw) < _MIN_NEW_PASSWORD_LEN:
            error = f"New password must be at least {_MIN_NEW_PASSWORD_LEN} characters."
        elif new_pw != confirm_pw:
            error = "New password and confirmation do not match."
        elif new_pw == current_pw:
            error = "Choose a new password that is different from your current one."
        else:
            new_hash = generate_password_hash(new_pw, method="pbkdf2:sha256")
            if not update_user_password(username, new_hash):
                error = "Could not update password. Try again or contact support."
            else:
                log_audit(
                    batch_id="SECURITY",
                    username=username,
                    action_type="password_change",
                    details="Review user changed their password",
                )
                flash("Your password was updated. Use it next time you log in.", "success")
                q = f"?next={quote(back_review)}" if back_review else ""
                return redirect(url_for("change_password") + q)

    csrf_token = generate_csrf_token()
    return render_template_string(
        """
        <!DOCTYPE html>
        <html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Change password — Eyre's Meat Chop</title>
        <style>
            body { font-family: system-ui, sans-serif; background: #f6f7f9; margin: 0; padding: 2rem; }
            .box { max-width: 440px; margin: 0 auto; background: #fff; padding: 1.5rem; border-radius: 8px;
                    box-shadow: 0 1px 3px rgba(0,0,0,.1); }
            h2 { margin-top: 0; }
            label { display: block; margin-top: 0.75rem; font-weight: 500; }
            input[type=password] { width: 100%; box-sizing: border-box; padding: 0.5rem; margin-top: 0.25rem; }
            button { margin-top: 1rem; padding: 0.5rem 1rem; background: #1a73e8; color: #fff; border: none;
                     border-radius: 4px; cursor: pointer; }
            .err { color: #c5221f; }
            .meta { color: #5f6368; font-size: 0.9rem; margin-top: 1rem; }
            .flash.success { background: #e6f4ea; color: #137333; padding: 0.65rem 1rem; border-radius: 6px; margin-bottom: 1rem; }
        </style></head><body>
        <div class="box">
        <h2>Change password</h2>
        <p class="meta">Signed in as <b>{{ username }}</b></p>
        {% with messages = get_flashed_messages(with_categories=true) %}
          {% if messages %}
            {% for category, message in messages %}
              <div class="flash {{ category }}">{{ message }}</div>
            {% endfor %}
          {% endif %}
        {% endwith %}
        {% if error %}<p class="err">{{ error }}</p>{% endif %}
        <form method="post">
            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
            {% if back_review %}<input type="hidden" name="next" value="{{ back_review }}">{% endif %}
            <label>Current password</label>
            <input type="password" name="current_password" autocomplete="current-password" required>
            <label>New password (min {{ min_len }} characters)</label>
            <input type="password" name="new_password" autocomplete="new-password" required minlength="{{ min_len }}">
            <label>Confirm new password</label>
            <input type="password" name="new_password_confirm" autocomplete="new-password" required minlength="{{ min_len }}">
            <button type="submit">Update password</button>
        </form>
        <p class="meta"><a href="{{ url_for('logout') }}">Logout</a>
        {% if back_review %} · <a href="{{ back_review }}">Back to review</a>{% endif %}</p>
        </div>
        </body></html>
        """,
        username=username,
        error=error,
        csrf_token=csrf_token,
        min_len=_MIN_NEW_PASSWORD_LEN,
        back_review=back_review,
    )


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
    try:
        item_map_count = count_item_map_rows()
    except Exception:
        item_map_count = -1

    has_pending = any((r["status"] == "pending") for r in items)

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
                button.btn-submit { background: #202124; color: #fff; border: none; padding: 0.5rem 1rem; border-radius: 4px; cursor: pointer; font-size: 0.875rem; margin-top: 0.5rem; }
                .row-apply { max-width: 22rem; }
                .choice-row { display: flex; flex-direction: column; gap: 0.35rem; margin-bottom: 0.5rem; font-size: 0.875rem; }
                .choice-row label { cursor: pointer; }
                .pw-label { display: block; font-size: 0.8rem; font-weight: 600; margin-top: 0.25rem; }
                .pw-input { width: 100%; max-width: 16rem; box-sizing: border-box; padding: 0.35rem 0.5rem; margin: 0.2rem 0 0.35rem 0; }
                button:disabled { opacity: 0.5; cursor: not-allowed; }
                .meta { color: #5f6368; font-size: 0.9rem; }
                .pill { display: inline-block; padding: 0.15rem 0.5rem; border-radius: 999px; font-size: 0.8rem; }
                .pill.pending { background: #fef7e0; color: #b06000; }
                .pill.done { background: #e8f0fe; color: #1967d2; }
                .callout { background: #e8f0fe; border: 1px solid #1967d2; border-radius: 8px; padding: 1rem; margin: 1rem 0; font-size: 0.95rem; line-height: 1.5; }
                .batch-footer { margin-top: 1rem; padding: 1rem 1.25rem; background: #fff; border: 1px solid #dadce0; border-radius: 8px; max-width: 28rem; box-shadow: 0 1px 3px rgba(0,0,0,.06); }
                .batch-footer .pw-input { max-width: 20rem; }
            </style>
        </head>
        <body>
        <div class="wrap">
        <h2>Eyre's Meat Chop — stock reconciliation</h2>
        <p class="meta">Batch <code>{{ batch_id }}</code> &nbsp;|&nbsp; Logged in as <b>{{ username }}</b>
           &nbsp;|&nbsp; <a href="{{ url_for('change_password', next='/review/' ~ batch_id) }}">Change password</a>
           &nbsp;|&nbsp; <a href="{{ url_for('logout') }}">Logout</a></p>
        <p>QBO quantity is the default source of truth in reports. For <strong>each pending line</strong>, choose an action, then enter <strong>your login password</strong> once and click <strong>Submit batch</strong> to apply all choices (logged under your account).</p>
        <p class="meta">Environment <code>{{ qbo_environment }}</code> — Loyverse↔QBO pairs in <code>item_map</code> right now: <strong>{{ item_map_count if item_map_count >= 0 else "?" }}</strong></p>

        {% if items|length == 0 %}
        <div class="callout">
            <strong>Why is this batch empty?</strong>
            <ul style="margin:0.5rem 0 0 1rem;">
                <li>The nightly job only compares products that are <strong>linked in the database</strong> (<code>item_map</code>). It does <em>not</em> compare your full Loyverse catalog to your full QBO list by name.</li>
                <li>If <code>item_map</code> is empty (0 pairs), every batch will have <strong>no discrepancy rows</strong> even when the two UIs look different.</li>
                <li>If pairs exist but quantities match (per API), you also get no rows.</li>
            </ul>
            <p style="margin:0.75rem 0 0 0;"><strong>Fix:</strong> On Render → your worker or web → <strong>Shell</strong>, run:</p>
            <pre style="background:#fff;padding:0.5rem;border-radius:4px;overflow:auto;">python sync_items_loyverse_to_qbo.py
python check_mappings.py</pre>
            Then run a new report: <code>python nightly_sync.py</code> (or wait for cron). Open the <strong>new</strong> email link / batch id.
        </div>
        {% endif %}

        {% with messages = get_flashed_messages(with_categories=true) %}
          {% if messages %}
            {% for category, message in messages %}
              <div class="flash {{ category }}">{{ message }}</div>
            {% endfor %}
          {% endif %}
        {% endwith %}

        {% if has_pending %}
        <form method="post" action="{{ url_for('review_apply_batch', batch_id=batch_id) }}">
            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        {% endif %}
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
                        <div class="choice-row">
                            <label><input type="radio" name="reconcile_action_{{ row['id'] }}" value="LOYVERSE" required> Fix Loyverse (→ QBO {{ row['qbo_qty'] }})</label>
                            <label><input type="radio" name="reconcile_action_{{ row['id'] }}" value="QBO"> Fix QBO (→ Loyverse {{ row['loyverse_qty'] }})</label>
                            <label><input type="radio" name="reconcile_action_{{ row['id'] }}" value="IGNORE"> Do nothing</label>
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
        {% if has_pending %}
            <div class="batch-footer">
                <label class="pw-label" for="batch-pw">Your password (confirm identity for this batch)</label>
                <input id="batch-pw" class="pw-input" type="password" name="confirm_password" autocomplete="current-password" required placeholder="Same password as login">
                <div style="margin-top:0.75rem;"><button type="submit" class="btn-submit">Submit batch</button></div>
            </div>
        </form>
        {% endif %}

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
        item_map_count=item_map_count,
        qbo_environment=settings.QBO_ENVIRONMENT,
        has_pending=has_pending,
    )


def _apply_one_reconcile_line(batch_id: str, item_id: int, action: str, username: str, who_prefix: str) -> None | str:
    """
    Apply a single reconcile action. Returns None on success, or an error message string.
    """
    row = get_reconcile_item(batch_id, item_id)
    if not row:
        return "Row not found."
    if row["status"] != "pending":
        return "Line already processed."

    if action == "IGNORE":
        complete_reconcile_item(batch_id, item_id, "IGNORE", username, "ignored")
        log_audit(
            batch_id=batch_id,
            reconcile_item_id=item_id,
            username=username,
            action_type="apply_ignore",
            new_value="IGNORE",
            details=who_prefix + "No inventory changes (Do nothing).",
        )
        return None

    if action == "LOYVERSE":
        try:
            fix_loyverse_to_match_qbo(row)
            complete_reconcile_item(batch_id, item_id, "LOYVERSE", username, "applied")
            log_audit(
                batch_id=batch_id,
                reconcile_item_id=item_id,
                username=username,
                action_type="apply_loyverse",
                new_value="LOYVERSE",
                details=who_prefix + "Loyverse stock set to QBO QtyOnHand.",
            )
            return None
        except Exception as e:
            return str(e)

    if action == "QBO":
        try:
            fix_qbo_to_match_loyverse(row)
            complete_reconcile_item(batch_id, item_id, "QBO", username, "applied")
            log_audit(
                batch_id=batch_id,
                reconcile_item_id=item_id,
                username=username,
                action_type="apply_qbo",
                new_value="QBO",
                details=who_prefix + "QBO QtyOnHand set to Loyverse quantity.",
            )
            return None
        except Exception as e:
            return str(e)

    return "Invalid action."


@app.route("/review/<batch_id>/apply-batch", methods=["POST"])
def review_apply_batch(batch_id):
    redir = _review_post_guard(batch_id)
    if redir is not None:
        return redir

    username = current_user()
    password = request.form.get("confirm_password", "")

    if not verify_logged_in_password(password):
        flash("Password incorrect — batch not applied. Try again.", "error")
        return redirect(url_for("review_batch", batch_id=batch_id))

    items = get_batch_items(batch_id)
    pending = [r for r in items if r["status"] == "pending"]
    if not pending:
        flash("No pending lines in this batch.", "error")
        return redirect(url_for("review_batch", batch_id=batch_id))

    who = f"User {username} re-authenticated (batch submit); "

    missing = []
    actions: dict[int, str] = {}
    for r in pending:
        rid = int(r["id"])
        key = f"reconcile_action_{rid}"
        raw = (request.form.get(key) or "").strip().upper()
        if raw not in ("LOYVERSE", "QBO", "IGNORE"):
            missing.append(r.get("item_name") or str(rid))
        if raw in ("LOYVERSE", "QBO", "IGNORE"):
            actions[rid] = raw

    if missing:
        flash(
            "Select an action (Fix Loyverse, Fix QBO, or Do nothing) for every pending line before submitting.",
            "error",
        )
        return redirect(url_for("review_batch", batch_id=batch_id))

    errors: list[str] = []
    ok = 0
    for rid, act in actions.items():
        err = _apply_one_reconcile_line(batch_id, rid, act, username, who)
        if err:
            errors.append(f"{rid}: {err}")
        else:
            ok += 1

    finalize_batch_if_all_items_done(batch_id, username)

    if errors:
        flash(
            f"Batch partially applied: {ok} line(s) OK. Errors: " + " | ".join(errors[:5]),
            "error",
        )
    else:
        flash(f"Batch applied: {ok} line(s). Logged as {username}.", "success")

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