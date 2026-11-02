# FastPOS backend

Django 5 + DRF backend for a supermarket self-checkout kiosk. A customer scans a product, places
it on the kiosk's built-in scale, the server verifies the weight against the product's expected
weight (theft detection), the customer pays with Waafi or E-Dahab, and the kiosk prints the receipt
and deactivates the EAS tag.

Two clients: the **kiosk app** (Flutter, `/home/eng-omar/posmobile`) (`/api/v1/kiosk/`, `Authorization: Kiosk <token>`) and the
**React admin panel** (`/api/v1/admin/`, `Authorization: Bearer <jwt>`). Swagger UI at `/api/docs/`,
OpenAPI file in `docs/openapi.yaml`.

## Quick start (SQLite, no MySQL/Redis needed)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python manage.py migrate
python manage.py seed_demo            # prints KIOSK_TOKEN, admin login, both staff codes + PIN
python manage.py runserver
```

Then, in a second shell, simulate a kiosk with a scale:

```bash
export KIOSK_TOKEN=<token printed by seed_demo>
python manage.py simulate_checkout --scenario honest --items 4
python manage.py simulate_checkout --scenario swap --items 3 --override --staff-code S1 --pin 1234
python manage.py simulate_checkout --scenario mixed --items 6 --pay EDAHAB --phone 252610000000   # payment declined
python manage.py simulate_checkout --phone 252618888888 --abandon-after 3   # kiosk payment timeout → cancel payment
python manage.py simulate_checkout --idle-cancel                            # kiosk idle timeout → cancel basket
```

Scenarios: `honest`, `swap` (expensive item placed for a cheap barcode), `double` (two units),
`empty` (nothing on the scale), `drift` (`--drift-g 40`), `mixed`. Mock payment rules: phone ending
`0000` is declined, `9999` times out, `8888` stays PENDING (for the payment-cancel path), anything
else succeeds.

`--abandon-after N` polls a payment N times, then cancels it (`payments/{pid}/cancel/`) and posts a
`PAYMENT_TIMEOUT` kiosk event; it exits 0 when the order is OPEN again. `--idle-cancel` skips payment,
cancels the basket with reason `IDLE_TIMEOUT` (no staff) and posts an `IDLE_TIMEOUT` event; it exits 0
when the order is CANCELLED. The two flags are mutually exclusive. Every run sends
`app_version="simulator"` and an all-OK device map in the heartbeat and posts `APP_STARTED`.

Demo data from `seed_demo`: store "Demo Mart" (USD, secondary currency SLSH at 8500 per USD), 17
products with Somali names (`name_so`), admin `admin@demo.test / admin1234`, staff `S1` PIN `1234`
and numeric kiosk staff `1001` PIN `1234` (Hodan Cabdi; change with `--kiosk-staff-code`). Re-running
it is safe but rotates the kiosk token.

Product images are stored under `media/` (`MEDIA_ROOT`); with `DEBUG=True` `runserver` serves them at
`/media/...`, and the kiosk lookup returns an absolute `image_url`. In production serve `MEDIA_ROOT`
from the web server.

## MySQL

Two one-time steps on the MySQL server, then point `.env` at it.

```bash
# 1. Database + user. Either run the script (creates user fastpos/fastpos) ...
sudo mysql < scripts/mysql_setup.sql
# ... or skip it and use root (or any user that can create databases) in DATABASE_URL.

# 2. Time zone tables. Django's TruncDate/CONVERT_TZ returns NULL without them, which breaks the
#    per-day sales report (500 "'NoneType' object has no attribute 'isoformat'").
mysql_tzinfo_to_sql /usr/share/zoneinfo | mysql -h 127.0.0.1 -P <port> -u root -p mysql

cp .env.example .env     # set DATABASE_URL=mysql://USER:PASSWORD@127.0.0.1:<port>/fastpos
python manage.py migrate && python manage.py seed_demo
```

Find the port with `grep -rn "^port" /etc/mysql/` (3306 by default; the Ubuntu install used for
development listens on 33061). `.env` is git-ignored; keep real passwords there, never in
`.env.example`. With root in `DATABASE_URL`, create the database first if the script was skipped:
`CREATE DATABASE fastpos CHARACTER SET utf8mb4;`.

The settings apply utf8mb4, `STRICT_TRANS_TABLES` and read-committed isolation automatically when
`DATABASE_URL` is MySQL. Because the test settings read `.env` too, `pytest` then runs on MySQL and
creates/drops `test_fastpos` (the user needs CREATE on it; the script grants this, root has it).
Leave `DATABASE_URL` unset to stay on SQLite. Until MySQL is reachable, `manage.py` and `pytest`
fail with "Can't connect" or "Access denied".

## Celery

Payment provider calls block for up to two minutes while the customer approves on their phone, so
they run in a Celery task and the kiosk polls. In dev `CELERY_TASK_ALWAYS_EAGER=true` runs tasks
inline (no Redis). In production run a Redis broker plus `celery -A config worker` and
`celery -A config beat` (beat reconciles stuck payments and cancels stale orders).
`docker-compose.yml` wires all of this up for machines with Docker.

## Kiosk flow

```
POST /api/v1/kiosk/orders/                                   -> order (OPEN)
GET  /api/v1/kiosk/products/lookup/?barcode=...              -> name, price, expected_weight_g, tolerance_g
POST /api/v1/kiosk/orders/{id}/items/  {barcode, measured_weight_g}   (+ Idempotency-Key header)
       201 item added | 422 WEIGHT_MISMATCH {verification_id, expected, measured, tolerance, delta}
POST /api/v1/kiosk/orders/{id}/items/override/ {verification_id, staff_code, pin, reason}
DELETE /api/v1/kiosk/orders/{id}/items/{item_id}/ {staff_code, pin}
POST /api/v1/kiosk/orders/{id}/payments/ {method: WAAFI|EDAHAB, customer_phone}   -> 202 PENDING
GET  /api/v1/kiosk/orders/{id}/payments/{pid}/               poll until SUCCESS | FAILED
GET  /api/v1/kiosk/orders/{id}/receipt/                      once PAID
POST /api/v1/kiosk/orders/{id}/events/ {type: RECEIPT_PRINTED | EAS_DEACTIVATED}
POST /api/v1/kiosk/orders/{id}/cancel/ {reason, staff_code?, pin?}   staff needed with items unless reason=IDLE_TIMEOUT
POST /api/v1/kiosk/orders/{id}/payments/{pid}/cancel/       abandon a PENDING payment -> FAILED, order OPEN
                                                            (409 PAYMENT_NOT_PENDING if it already succeeded)
POST /api/v1/kiosk/heartbeat/  optional {app_version, devices: {scanner|scale|printer|eas: OK|WARN|FAULT|OFF}}
       -> kiosk, store (currency, tax_rate, secondary_currency, secondary_rate), server_time
POST /api/v1/kiosk/staff/verify/ {staff_code, pin}          -> {id, name, role, staff_code} | 403 INVALID_STAFF_PIN
POST /api/v1/kiosk/events/ {type, order?, detail?}          -> 201; STAFF_CALLED, UNKNOWN_BARCODE, PAYMENT_TIMEOUT,
       TERMINAL_LOCKED, TERMINAL_RELEASED, WEIGHT_RETRY_LIMIT, IDLE_TIMEOUT, HARDWARE_FAULT, APP_STARTED
```

The lookup response also carries `name_so` and `image_url` (both nullable).

Weight rule: `tolerance = product.weight_tolerance_g or store.default_weight_tolerance_g`;
`PASS` when `abs(measured - expected) <= tolerance`. Every attempt is stored in
`WeightVerification`; a failed attempt never becomes an order item unless staff override it.

Errors always look like `{"error": {"code": "WEIGHT_MISMATCH", "message": "...", "details": {}}}`.
Codes: `PRODUCT_NOT_FOUND`, `WEIGHT_MISMATCH`, `ORDER_NOT_OPEN`, `ORDER_NOT_PAID`, `EMPTY_ORDER`,
`INVALID_STAFF_PIN`, `STAFF_REQUIRED`, `ITEM_NOT_FOUND`, `VERIFICATION_NOT_FOUND`,
`VERIFICATION_NOT_OVERRIDABLE`, `PAYMENT_IN_PROGRESS`, `PAYMENT_FAILED`, `PAYMENT_NOT_PENDING`,
`ORDER_NOT_FOUND`, `VALIDATION_ERROR`,
`AUTHENTICATION_FAILED`, `PERMISSION_DENIED`, `NOT_FOUND`.

## Admin API (JWT, role ADMIN, scoped to the admin's store)

`POST /api/v1/auth/token/` (email + password), `POST /api/v1/auth/token/refresh/`, `GET /api/v1/admin/me/`.

| Resource | Endpoints |
|---|---|
| Stores (superuser) | `admin/stores/` (incl. `secondary_currency`, `secondary_rate`) |
| Kiosks | `admin/kiosks/`, `POST admin/kiosks/{id}/rotate-key/` (token shown once); read-only `app_version`, `device_status` |
| Staff | `admin/staff/`, `POST admin/staff/{id}/set-pin/` |
| Catalog | `admin/categories/`, `admin/products/` (`name_so`; multipart `image`, read-only `image_url`), `POST admin/products/import/` (CSV), `GET admin/products/export/` |
| Sales | `admin/orders/`, `admin/payments/`, `admin/weight-verifications/` (read-only, filterable) |
| Kiosk events | `admin/kiosk-events/?type=&kiosk=&created_after=&created_before=` (read-only, includes `kiosk_name`) |
| Reports | `admin/reports/sales-summary/?from=&to=`, `admin/reports/weight-incidents/?from=&to=` |

Superusers have no store and pass `?store=<id>`. CSV import columns:
`barcode,name,price,expected_weight_g,weight_tolerance_g,category,name_so` (upsert by barcode;
`name_so` is optional).

## Payment providers

`apps/payments/providers/` holds `MockProvider`, `WaafiProvider` (WaafiPay `API_PURCHASE`) and
`EdahabProvider` (`IssueInvoice` + `CheckInvoiceStatus`). `PAYMENT_PROVIDER_BACKEND=mock` routes
everything to the mock; set it to `live` and fill the `WAAFI_*` / `EDAHAB_*` variables once merchant
credentials arrive. Field names in the live providers are marked TODO to confirm against the
merchant documentation.

## Layout

```
config/            settings (base/dev/test/prod), urls, celery
apps/core          error envelope, permissions, store scoping, pagination, idempotency
apps/stores        Store, Kiosk, kiosk token auth, heartbeat, admin stores/kiosks
apps/accounts      User (ADMIN/STAFF, staff_code + PIN), JWT, admin staff/me
apps/catalog       Category, Product, lookup, admin CRUD + CSV import/export
apps/orders        Order, OrderItem, WeightVerification, services (weight check), kiosk + admin API
apps/payments      Payment, providers, Celery tasks, pay/poll/receipt/events, admin list
apps/reports       sales-summary, weight-incidents
apps/simulator     SimulatedScale, KioskClient, seed_demo, simulate_checkout
```

`pytest` runs the suite; `ruff check .` lints.
