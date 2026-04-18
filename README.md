# Eyre's Meat Chop — Loyverse ↔ QuickBooks Sync (Render Guide)

This project keeps **sales + inventory** aligned between **Loyverse POS** and **QuickBooks Online (QBO)**.

- **Sales happen in Loyverse** → we create a matching **QBO SalesReceipt** so QBO becomes the accounting truth.
- **Business sales entered as QBO Invoices** → we update **Loyverse stock** so it stays accurate.
- **Stock received/adjusted in QBO** → we update **Loyverse stock** near-live.
- A **nightly reconciliation** email is sent with a link to a hosted review page.

This README is a step-by-step deployment guide for **Render** for someone who doesn’t know their way around yet.

---

## What you will deploy on Render

You will create **three** services:

- **Web service**: hosts the review UI (`app.py`)
- **Worker**: runs live sync (`live_sync_runner.py`)
- **Cron job**: runs nightly sync + emails (`nightly_sync.py`)

There is a `render.yaml` in this repo that sets the commands; you mainly need to configure **Postgres** and **environment variables**.

---

## Before you start (accounts / keys you must have)

You will need:

- **Render** account
- **QuickBooks Online** developer app credentials
  - `QBO_CLIENT_ID`
  - `QBO_CLIENT_SECRET`
  - `QBO_REALM_ID`
  - `QBO_ACCESS_TOKEN`
  - `QBO_REFRESH_TOKEN`
- **Loyverse** API token
  - `LOYVERSE_ACCESS_TOKEN`
- Email (SMTP) credentials (to send nightly report)
  - `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`
  - `EMAIL_FROM`, `EMAIL_TO`

### Local secret files (next to `config.py`)

The app loads, in order:

1. `.env`
2. `.env.production` (values here **override** `.env` when both exist)

So you can keep real keys only in **`.env.production`** and still run scripts from any folder.

To use a single custom filename instead, set **`APP_ENV_FILE`** (e.g. `APP_ENV_FILE=.env.production`) in the system environment or shell.

---

## Step 1 — Push this repo to GitHub

1. Create a GitHub repository (private is fine).
2. Push this code to GitHub.

Render deploys directly from GitHub.

---

## Step 2 — Create a Render Postgres database

1. In Render, create a **PostgreSQL** database.
2. Copy the **Internal Database URL**.

You will set it as:

- `DATABASE_URL`

This makes the app persistent and safe across restarts.

---

## Step 3 — Create the Render services

### Option A (recommended): Blueprint deploy

1. In Render: **New +** → **Blueprint**
2. Select your GitHub repo
3. Render should detect `render.yaml` and propose creating:
   - web service
   - worker
   - cron

### Option B: Create services manually

If you create services manually, use these commands:

- **Web** start command:

```bash
gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120
```

- **Worker** start command:

```bash
python -u live_sync_runner.py
```

- **Cron** command:

```bash
python -u nightly_sync.py
```

---

## Step 4 — Set environment variables (Render dashboard)

Set these on **all three** services (web, worker, cron) unless noted.

### Required (core)
- **`APP_ENV`**: `production`
- **`DATABASE_URL`**: the Render Postgres internal URL
- **`SECRET_KEY`**: generate a long random string (keep it secret)
- **`QBO_ENVIRONMENT`**: `production` (or `sandbox` if you’re testing)
- **`QBO_CLIENT_ID`**
- **`QBO_CLIENT_SECRET`**
- **`QBO_REALM_ID`**
- **`QBO_ACCESS_TOKEN`**
- **`QBO_REFRESH_TOKEN`**
- **`LOYVERSE_ACCESS_TOKEN`**

### Required (nightly email)
Set these at least on the **cron** service (safe to set everywhere):
- **`SMTP_HOST`**
- **`SMTP_PORT`**: usually `587` (TLS) or `465` (SSL)
- **`SMTP_USERNAME`**
- **`SMTP_PASSWORD`**
- **`EMAIL_FROM`**
- **`EMAIL_TO`**: comma-separated list, e.g. `me@example.com,you@example.com`

### Required (review link in email)
- **`APP_BASE_URL`**: your public Render web URL, e.g. `https://eyres-meat-chop-review.onrender.com` (must match the web service hostname)

### Recommended
- **`DRY_RUN`**: `false` (production should write changes)
- **Review UI logins** (choose one approach):
  - **`REVIEW_USERNAME`** / **`REVIEW_PASSWORD`** — single reviewer, or
  - **`REVIEW_USERS_JSON`** — JSON array of objects `{ "username": "email@...", "password": "..." }` for multiple reviewers (set on the **web** service; do not commit this string to git)
- **`REVIEW_SEED_TOKEN`** (recommended): random secret; if set, `/seed-review-user` only works as `YOUR_APP_BASE_URL/seed-review-user?token=YOUR_TOKEN` so strangers cannot create accounts
- **`SESSION_COOKIE_SECURE`**: `true` (forces secure cookies; production already defaults to secure)

---

## Step 5 — First-time setup (review users)

After the web service is up and **`DATABASE_URL`** is set:

1. Add **`REVIEW_USERS_JSON`** (or **`REVIEW_USERNAME`** / **`REVIEW_PASSWORD`**) to the **web** service environment on Render.
2. Optionally set **`REVIEW_SEED_TOKEN`** and open once in a browser:
   - `https://YOUR-SERVICE.onrender.com/seed-review-user?token=YOUR_TOKEN`  
   Or omit the token if **`REVIEW_SEED_TOKEN`** is not set (less secure).
3. Remove or clear **`REVIEW_USERS_JSON`** from the dashboard after users are created so passwords are not stored long-term in env.

Alternatively, from a machine with the same **`DATABASE_URL`**, run:

```bash
python seed_review_users_json.py
```

Then log in at:

- `https://YOUR-SERVICE.onrender.com/login`

The nightly email links to `/login?next=/review/BATCH_ID`. After login, each discrepancy has **Fix Loyverse** (match QBO), **Fix QBO** (match Loyverse), or **Do nothing**.

---

## Step 6 — Inventory tracking in QBO (important)

For QBO to actually maintain `QtyOnHand`, items must be **inventory-tracked**.

This repo contains:
- `migrate_mapped_items_to_inventory.py`

### Safe run order

1. **Dry run** (no changes):
   - set `DRY_RUN=true`
   - run:

```bash
python migrate_mapped_items_to_inventory.py
```

2. **Apply for real**:
   - set `DRY_RUN=false`
   - run again:

```bash
python migrate_mapped_items_to_inventory.py
```

On Render, you can run one-off commands using the Render shell for the worker/web container.

---

## Step 7 — How “live sync” works (what runs continuously)

The worker (`live_sync_runner.py`) does three things:

1. **Collect new Loyverse receipts** → create QBO SalesReceipts
2. **Collect new QBO invoices** → update Loyverse stock (invoice line items)
3. **Collect QBO item QtyOnHand changes** → set Loyverse stock to match QBO

This is designed to be **idempotent** (it won’t double-create) using the database mappings + sync event store.

---

## Step 8 — Nightly reconciliation at 10pm

The cron job runs `nightly_sync.py`, which:

1. Syncs receipts Loyverse → QBO
2. Generates a reconciliation report
3. Emails it with a link to the review UI

### Set the cron time correctly

Render cron schedules are in **UTC**.

If you want “10pm local”, convert your timezone to UTC and update `render.yaml` (or the Render cron config).

---

## Step 9 — Quick verification checklist (do this once)

### A) Sales in Loyverse reduce QBO on-hand
1. Confirm items are inventory-tracked in QBO (Step 6).
2. Make a small test sale in Loyverse.
3. Wait for worker run (or restart worker).
4. In QBO:
   - find the SalesReceipt with `DocNumber` like `LOY-...`
   - confirm the item `QtyOnHand` decreased

### B) Invoice in QBO reduces Loyverse stock
1. Create a QBO invoice for a mapped item.
2. Wait for worker run.
3. Confirm Loyverse stock decreased.

### C) Receiving stock in QBO increases Loyverse stock
1. Adjust/receive inventory in QBO for a mapped item.
2. Wait for worker run.
3. Confirm Loyverse stock updated to match.

---

## Troubleshooting

### “Nothing is changing”
- Check Render logs for the **worker** service first.
- Confirm `DRY_RUN=false`.
- Confirm `DATABASE_URL` is set on **worker + cron + web**.

### QBO tokens expire
- This app persists refreshed tokens into the database table `qbo_oauth_tokens`.
- If the refresh token is revoked, you’ll need to re-authenticate and update secrets in Render.

### Duplicate items in QBO
Run:

```bash
python dedupe_qbo_items.py
```

and review the generated CSV reports in `reports/`.

