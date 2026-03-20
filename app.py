from flask import Flask, request, redirect, jsonify
import os
import secrets

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
    code = request.args.get("code")
    realm_id = request.args.get("realmId")

    # Do not expose sensitive data in the response body
    print("OAuth callback received", bool(code), bool(realm_id))

    return redirect("/success")

@app.route("/success")
def success():
    return "QuickBooks Connected Successfully"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)