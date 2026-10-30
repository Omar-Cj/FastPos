# FastPOS backend

Django 5 + DRF backend for a supermarket self-checkout kiosk. A customer scans a product, places
it on the kiosk's built-in scale, the server verifies the weight against the product's expected
weight (theft detection), the customer pays with Waafi or E-Dahab, and the kiosk prints the receipt
and deactivates the EAS tag.

Two clients: the **Android kiosk app** (`/api/v1/kiosk/`, `Authorization: Kiosk <token>`) and the
**React admin panel** (`/api/v1/admin/`, `Authorization: Bearer <jwt>`). Swagger UI at `/api/docs/`,
OpenAPI file in `docs/openapi.yaml`.

## Quick start (SQLite, no MySQL/Redis needed)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python manage.py migrate
python manage.py seed_demo            # prints KIOSK_TOKEN, admin login, staff code + PIN
python manage.py runserver
```

Then, in a second shell, simulate a kiosk with a scale:

```bash
export KIOSK_TOKEN=<token printed by seed_demo>
python manage.py simulate_checkout --scenario honest --items 4
python manage.py simulate_checkout --scenario swap --items 3 --override --staff-code S1 --pin 1234
python manage.py simulate_checkout --scenario mixed --items 6 --pay EDAHAB --phone 252610000000   # payment declined
```

Scenarios: `honest`, `swap` (expensive item placed for a cheap barcode), `double` (two units),
`empty` (nothing on the scale), `drift` (`--drift-g 40`), `mixed`. Mock payment rules: phone ending
`0000` is declined, `9999` times out, anything else succeeds.

## MySQL

```bash
sudo mysql < scripts/mysql_setup.sql           # creates db + user fastpos/fastpos
cp .env.example .env                           # DATABASE_URL already points at MySQL
python manage.py migrate && python manage.py seed_demo
```

Tests run on SQLite by default (`pytest`). To run them on MySQL:
`DATABASE_URL=mysql://fastpos:fastpos@127.0.0.1:3306/fastpos pytest`.

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
POST /api/v1/kiosk/orders/{id}/cancel/
POST /api/v1/kiosk/heartbeat/
```

Weight rule: `tolerance = product.weight_tolerance_g or store.default_weight_tolerance_g`;
`PASS` when `abs(measured - expected) <= tolerance`. Every attempt is stored in
`WeightVerification`; a failed attempt never becomes an order item unless staff override it.

Errors always look like `{"error": {"code": "WEIGHT_MISMATCH", "message": "...", "details": {}}}`.
Codes: `PRODUCT_NOT_FOUND`, `WEIGHT_MISMATCH`, `ORDER_NOT_OPEN`, `ORDER_NOT_PAID`, `EMPTY_ORDER`,
`INVALID_STAFF_PIN`, `STAFF_REQUIRED`, `ITEM_NOT_FOUND`, `VERIFICATION_NOT_FOUND`,
`VERIFICATION_NOT_OVERRIDABLE`, `PAYMENT_IN_PROGRESS`, `PAYMENT_FAILED`, `VALIDATION_ERROR`,
`AUTHENTICATION_FAILED`, `PERMISSION_DENIED`, `NOT_FOUND`.

## Admin API (JWT, role ADMIN, scoped to the admin's store)

`POST /api/v1/auth/token/` (email + password), `POST /api/v1/auth/token/refresh/`, `GET /api/v1/admin/me/`.

| Resource | Endpoints |
|---|---|
| Stores (superuser) | `admin/stores/` |
| Kiosks | `admin/kiosks/`, `POST admin/kiosks/{id}/rotate-key/` (token shown once) |
| Staff | `admin/staff/`, `POST admin/staff/{id}/set-pin/` |
| Catalog | `admin/categories/`, `admin/products/`, `POST admin/products/import/` (CSV), `GET admin/products/export/` |
| Sales | `admin/orders/`, `admin/payments/`, `admin/weight-verifications/` (read-only, filterable) |
| Reports | `admin/reports/sales-summary/?from=&to=`, `admin/reports/weight-incidents/?from=&to=` |

Superusers have no store and pass `?store=<id>`. CSV import columns:
`barcode,name,price,expected_weight_g,weight_tolerance_g,category` (upsert by barcode).

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
