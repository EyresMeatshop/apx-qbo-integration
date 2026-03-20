from flask import Flask, request, redirect, jsonify
import os
import secrets
import requests
import base64

app = Flask(__name__)

@app.after_request
def apply_security_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response

@app.route("/")
def home():
    return "APX Omnipoint QBO Integration Running"

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

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
        return f"OAuth error: {error} | details: {dict(request.args)}", 400

    auth_code = request.args.get("code")
    realm_id = request.args.get("realmId")

    if not auth_code:
        return f"Missing auth code. Params received: {dict(request.args)}", 400

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
        return f"Token exchange failed: {r.status_code} | {r.text}", 400

    token_json = r.json()

    access_token = token_json.get("access_token", "")
    refresh_token = token_json.get("refresh_token", "")

    return f"""
    <h3>Connected successfully</h3>
    <p>Copy these into your <b>.env.production</b> file:</p>
    <pre>
QBO_REALM_ID={realm_id}
QBO_ACCESS_TOKEN={access_token}
QBO_REFRESH_TOKEN={refresh_token}
    </pre>
    """

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