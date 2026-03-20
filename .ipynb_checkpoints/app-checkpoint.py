import os
import base64
import requests

from flask import Flask, redirect, request
from intuitlib.client import AuthClient
from intuitlib.enums import Scopes
from config import settings

os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)
os.environ.pop("NO_PROXY", None)
from flask import Flask, request, redirect, jsonify
import os
import secrets

app = Flask(__name__)

# -------------------------
# SECURITY HEADERS (REQUIRED)
# -------------------------
@app.after_request
def apply_security_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response

# -------------------------
# HOME ROUTE
# -------------------------
@app.route("/")
def home():
    return "APX Omnipoint QBO Integration Running"

# -------------------------
# HEALTH CHECK
# -------------------------
@app.route("/health")
def health():
    return jsonify({"status": "ok"})

# -------------------------
# OAUTH START
# -------------------------
@app.route("/connect")
def connect():
    state = secrets.token_urlsafe(16)

    auth_url = f"https://appcenter.intuit.com/connect/oauth2" \
               f"?client_id={os.getenv('QBO_CLIENT_ID')}" \
               f"&redirect_uri={os.getenv('QBO_REDIRECT_URI')}" \
               f"&response_type=code" \
               f"&scope=com.intuit.quickbooks.accounting" \
               f"&state={state}"

    return redirect(auth_url)

# -------------------------
# OAUTH CALLBACK (CRITICAL)
# -------------------------
@app.route("/qbo-callback")
def callback():
    code = request.args.get("code")
    realm_id = request.args.get("realmId")

    # DO NOT return tokens here
    # DO NOT display sensitive info

    print("OAuth success - code received")  # safe log

    # Redirect instead of showing data (REQUIRED by Intuit)
    return redirect("/success")

# -------------------------
# SUCCESS PAGE
# -------------------------
@app.route("/success")
def success():
    return "QuickBooks Connected Successfully"

# -------------------------
# RUN APP
# -------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
app = Flask(__name__)
app.secret_key = settings.SECRET_KEY


def build_auth_client():
    return AuthClient(
        client_id=settings.QBO_CLIENT_ID,
        client_secret=settings.QBO_CLIENT_SECRET,
        redirect_uri=settings.QBO_REDIRECT_URI,
        environment=settings.QBO_ENVIRONMENT,
    )


@app.route("/")
def home():
    return '<h2>QBO Connect</h2><a href="/connect-qbo">Connect QuickBooks Online</a>'


@app.route("/connect-qbo")
def connect_qbo():
    print("CLIENT ID:", settings.QBO_CLIENT_ID)
    print("ENVIRONMENT:", settings.QBO_ENVIRONMENT)
    print("REDIRECT URI:", settings.QBO_REDIRECT_URI)

    auth_client = build_auth_client()
    auth_url = auth_client.get_authorization_url([Scopes.ACCOUNTING])
    print("AUTH URL:", auth_url)

    return redirect(auth_url)


@app.route("/qbo-callback")
def qbo_callback():
    error = request.args.get("error")
    if error:
        return f"OAuth error: {error} | details: {dict(request.args)}", 400

    auth_code = request.args.get("code")
    realm_id = request.args.get("realmId")

    if not auth_code:
        return f"Missing auth code. Params received: {dict(request.args)}", 400

    token_url = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"

    basic = f"{settings.QBO_CLIENT_ID}:{settings.QBO_CLIENT_SECRET}"
    basic_b64 = base64.b64encode(basic.encode("utf-8")).decode("utf-8")

    headers = {
        "Accept": "application/json",
        "Authorization": f"Basic {basic_b64}",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    data = {
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": settings.QBO_REDIRECT_URI,
    }

    session = requests.Session()
    session.trust_env = False

    try:
        resp = session.post(token_url, headers=headers, data=data, timeout=30)
    except Exception as e:
        return f"Token request failed before response: {e}", 500

    if not resp.ok:
        return f"Token exchange failed: {resp.status_code}<br><pre>{resp.text}</pre>", 500

    token_json = resp.json()

    access_token = token_json.get("access_token", "")
    refresh_token = token_json.get("refresh_token", "")

    return f"""
    <h3>Connected successfully</h3>
    <p>Copy these into your .env file:</p>
    <pre>
QBO_REALM_ID={realm_id}
QBO_ACCESS_TOKEN={access_token}
QBO_REFRESH_TOKEN={refresh_token}
    </pre>
    """


if __name__ == "__main__":
    app.run(port=5000, debug=True)