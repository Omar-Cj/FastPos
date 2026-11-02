# FastPOS backend – progress report

_Last updated: 2026-10-31_

This file records what has been decided, built and verified on the FastPOS backend so far, and what
is still open. It is the hand-over document for the Android kiosk developer, the React admin
developer and whoever picks up the backend next. The README covers how to run things; this file
covers what exists and why.

---

## 1. Project summary

FastPOS is an automated self-checkout POS for supermarkets. The hardware is an all-in-one Android
POS terminal (SCPOS AI-1 PDN, Android 11) with a built-in CAS scale, barcode scanner and 80 mm
receipt printer.

Customer flow the backend supports today:

1. Customer scans a product barcode.
2. Customer places the item on the built-in scale. The kiosk sends the measured weight.
3. The **server** compares the measured weight with the product's expected weight. A match adds the
   item to the basket. A mismatch is rejected and recorded as a theft-detection incident. Staff can
   override a rejection with their code and PIN.
4. Customer pays with **Waafi (Hormuud)** or **E-Dahab (Somtel)**. No cash in the MVP.
5. On successful payment the order gets a gapless receipt number, the kiosk prints the receipt and
   deactivates the EAS security tag, and reports both events back.

AI-based detection (camera, product recognition) is explicitly **out of scope** for the MVP.

---

## 2. Decisions taken

| Topic | Decision | Reason |
|---|---|---|
| Framework | Django 5.2 + Django REST Framework 3.16 | Team preference, fast MVP |
| Database | **MySQL 8** (utf8mb4, STRICT_TRANS_TABLES, read committed). SQLite fallback for dev and tests | User decision; SQLite keeps local dev friction-free |
| Admin panel | **React/TypeScript** against a JWT admin API. Django admin kept only as a superuser fallback | User decision |
| Weight verification | **Server-side** | Single source of truth, auditable, tamper-resistant |
| Payment methods | Waafi and E-Dahab only | User decision |
| Catalog scope | Fixed-weight packaged items managed in our admin | Keeps weight rule simple: one expected weight per product |
| Kiosk identity | Per-device token, SHA-256 hashed in DB, sent as `Authorization: Kiosk <token>` | No user login on the kiosk |
| Admin identity | Email + password → JWT (simplejwt), roles ADMIN / STAFF | Standard for a SPA |
| Async | Celery 5. Eager (inline) in dev/test, Redis broker in prod | Payment provider calls can block for up to 2 minutes |
| Android app | Not available yet, so a **scale + kiosk simulator** stands in for it | Lets us test the full flow end to end without hardware |
| Errors | Uniform envelope `{"error": {"code", "message", "details"}}` | One parser on both clients |

---

## 3. What has been built

### 3.1 Repository layout

```
config/            settings (base / dev / test / prod), urls, celery
apps/core          error envelope, permissions, store scoping mixins, pagination, idempotency
apps/stores        Store, Kiosk, kiosk token auth, heartbeat, admin stores/kiosks
apps/accounts      custom User (ADMIN/STAFF, staff_code + PIN), JWT auth, admin staff + me
apps/catalog       Category, Product, barcode lookup, admin CRUD, CSV import/export
apps/orders        Order, OrderItem, WeightVerification, weight-check services, kiosk + admin API
apps/payments      Payment, provider adapters, Celery tasks, pay/poll/receipt/events, admin list
apps/reports       sales-summary, weight-incidents
apps/simulator     SimulatedScale, KioskClient, seed_demo, simulate_checkout
scripts/           mysql_setup.sql
docs/              openapi.yaml (62 operations)
```

Supporting files: `requirements.txt`, `requirements-dev.txt`, `.env.example`, `pytest.ini`,
`pyproject.toml` (ruff, line length 120), `Dockerfile`, `docker-compose.yml` (mysql, redis, web,
worker, beat), `README.md`.

### 3.2 Data model

Every table except User and Store is scoped to a Store.

**Store** – name, address, currency (USD), `secondary_currency` (optional 3–4 letter code, e.g.
`SLSH`) + `secondary_rate` (units of secondary currency per 1 unit of `currency`, required and > 0
when a code is set), timezone, tax_rate, `default_weight_tolerance_g` (10), `next_receipt_number`,
is_active. Holds the per-store receipt counter.

**Kiosk** – store, name, serial_number, `key_hash` (SHA-256 of the device token), key_prefix,
is_active, last_seen_at, `app_version` and `device_status` (JSON map `{scanner|scale|printer|eas:
OK|WARN|FAULT|OFF}`), both written by the heartbeat. The plain token is shown exactly once when
issued or rotated.

**User** (custom, email as username) – role ADMIN or STAFF, optional store FK (superusers have none),
`staff_code` (unique per store) and `pin_hash` for on-kiosk overrides.

**Category** – store, name (unique per store), `name_so` (optional Somali name).

**Product** – store, category, barcode (unique per store), name, `name_so` (optional Somali name),
`image` (optional, stored under `MEDIA_ROOT/products/%Y/%m/`; also the Phase-2 reference image),
price, `expected_weight_g`, `weight_tolerance_g` (nullable, falls back to the store default),
`requires_weight_check`, `has_eas_tag`, is_active.

**Order** – UUID pk, store, kiosk, status, subtotal / tax / total, `receipt_number` (unique per
store, assigned only on payment), paid_at, cancelled_at, cancel_reason, receipt_printed_at,
eas_deactivated_at.

**WeightVerification** – one row per weigh attempt: order, kiosk, product, expected / tolerance /
measured / delta, `result` PASS or FAIL, `outcome` ADDED, REJECTED or OVERRIDDEN, override_by,
override_reason. This is the theft-detection audit trail.

**OrderItem** – order, product, price and weight snapshots taken at scan time, measured_weight_g,
`weight_status` PASSED / OVERRIDDEN / SKIPPED, one-to-one link to its WeightVerification,
overridden_by, `client_request_id` (idempotency, unique per order).

**Payment** – UUID pk, order, method WAAFI or EDAHAB, amount, currency, customer_phone, status
PENDING / SUCCESS / FAILED, failure_reason, provider_reference, provider request/response JSON
(API key redacted), `client_request_id`, initiated_at, completed_at.

**KioskEvent** – store, kiosk, optional order, `type` (STAFF_CALLED, UNKNOWN_BARCODE,
PAYMENT_TIMEOUT, TERMINAL_LOCKED, TERMINAL_RELEASED, WEIGHT_RETRY_LIMIT, IDLE_TIMEOUT,
HARDWARE_FAULT, APP_STARTED), `detail` JSON, created_at. Indexed on `(store, created_at)` and
`(kiosk, type)`. Operational log the kiosk reports; separate from the per-order
RECEIPT_PRINTED / EAS_DEACTIVATED events, which set timestamps on the Order.

Migrations: `0001_initial` per app, plus `catalog.0002_name_so_image`,
`stores.0002_secondary_currency_device_status` and `orders.0002_kioskevent`.

### 3.3 Weight verification rule

```
tolerance = product.weight_tolerance_g or store.default_weight_tolerance_g
delta     = measured_weight_g - product.expected_weight_g
PASS  iff  abs(delta) <= tolerance
```

- PASS → WeightVerification(PASS, ADDED) + OrderItem(PASSED). Response 201.
- FAIL → WeightVerification(FAIL, REJECTED), **no item**. Response 422 `WEIGHT_MISMATCH` with
  `verification_id`, expected, measured, tolerance and delta so the kiosk can show the customer.
- Override → staff enters `staff_code` + `pin`; verification becomes OVERRIDDEN and an
  OrderItem(OVERRIDDEN) is created with `overridden_by` set. Wrong PIN → 403 `INVALID_STAFF_PIN`.
- `requires_weight_check = False` (SIM cards, vouchers) → item added as SKIPPED, no verification row.

The mismatch exception is raised **after** the transaction commits so the REJECTED row is always
persisted. Every service function locks the Order row (`select_for_update`) so two kiosk requests
on the same order cannot interleave.

### 3.4 Order state machine

```
OPEN ──initiate payment──▶ AWAITING_PAYMENT ──provider SUCCESS──▶ PAID
 ▲                               │
 └────────provider FAILED────────┘
OPEN ──cancel / stale──▶ CANCELLED
```

- Items can only be added, overridden or removed while OPEN (else 409 `ORDER_NOT_OPEN`).
- `finalize()` runs atomically with locks on the Order and the Store, assigns the next gapless
  receipt number, and is idempotent.
- Celery beat cancels orders left OPEN for more than `STALE_ORDER_MINUTES` (30).

### 3.5 Payments

- Provider protocol with two methods: `charge` and `check_status`.
- `MockProvider` for dev and tests. Phone ending `0000` is declined, `9999` times out, `8888`
  stays PENDING (on both `charge` and `check_status`, so the kiosk cancel path can be exercised),
  anything else succeeds. `PAYMENT_MOCK_OUTCOME` and `PAYMENT_MOCK_DELAY_SECONDS` tune it.
- `WaafiProvider` (WaafiPay `API_PURCHASE`, success on responseCode `2001`) and `EdahabProvider`
  (`IssueInvoice` + `CheckInvoiceStatus`, SHA-256 request hash). Field names are marked TODO until
  merchant documentation is confirmed.
- `PAYMENT_PROVIDER_BACKEND=mock|live` picks the registry.
- `initiate_payment` recomputes totals, creates the Payment, moves the order to AWAITING_PAYMENT and
  schedules `process_payment` on commit. The kiosk gets 202 and polls the payment status.
- `reconcile_pending_payments` (every 60 s) re-checks PENDING payments older than 3 minutes and
  gives up after 30 minutes, failing the payment and reopening the order.
- `abandon_payment` (kiosk `payments/{pid}/cancel/`): PENDING → FAILED "Abandoned by customer" and
  the order goes back to OPEN; already FAILED → returned unchanged; SUCCESS → 409
  `PAYMENT_NOT_PENDING`.
- Receipt endpoint groups lines by product and is only available once PAID.

### 3.6 Idempotency

Kiosk writes (add item, initiate payment) accept an `Idempotency-Key` header (UUID). It is stored
as `client_request_id`. A replay returns the original result with 200 instead of 201/202, so a
kiosk that lost the response can safely retry.

### 3.7 Kiosk API (`/api/v1/kiosk/`, `Authorization: Kiosk <token>`)

```
POST   heartbeat/                              optional {app_version, devices}
POST   staff/verify/                           {staff_code, pin}          → {id, name, role, staff_code}
GET    products/lookup/?barcode=               + name_so, image_url
POST   orders/
GET    orders/{id}/
POST   orders/{id}/items/                      {barcode, measured_weight_g}
POST   orders/{id}/items/override/             {verification_id, staff_code, pin, reason}
DELETE orders/{id}/items/{item_id}/            {staff_code, pin}
POST   orders/{id}/cancel/                     {reason, staff_code?, pin?}  (no staff for IDLE_TIMEOUT)
POST   orders/{id}/payments/                   {method, customer_phone}   → 202
GET    orders/{id}/payments/{payment_id}/
POST   orders/{id}/payments/{payment_id}/cancel/                          → PaymentStatus
GET    orders/{id}/receipt/
POST   orders/{id}/events/                     {type: RECEIPT_PRINTED | EAS_DEACTIVATED}
POST   events/                                 {type, order?, detail?}    → 201 (KioskEvent)
```

Details of the 2026-10-31 additions are in section 3.11.

### 3.8 Admin API (`/api/v1/admin/`, `Authorization: Bearer <jwt>`, role ADMIN)

```
POST   /api/v1/auth/token/            email + password
POST   /api/v1/auth/token/refresh/
GET    admin/me/
       admin/stores/                  superuser only; + secondary_currency, secondary_rate
       admin/kiosks/                  + POST admin/kiosks/{id}/rotate-key/; + app_version, device_status (read-only)
       admin/staff/                   + POST admin/staff/{id}/set-pin/
       admin/categories/              + name_so
       admin/products/                + POST products/import/ (CSV), GET products/export/;
                                      + name_so, multipart `image` (write), `image_url` (read)
       admin/orders/                  read-only, filterable
       admin/payments/                read-only, filterable
       admin/weight-verifications/    read-only, filterable
GET    admin/kiosk-events/            read-only, ?type=&kiosk=&created_after=&created_before=, + kiosk_name
GET    admin/reports/sales-summary/?from=&to=
GET    admin/reports/weight-incidents/?from=&to=
```

Store admins are automatically scoped to their own store. Superusers have no store and pass
`?store=<id>`. Cross-store access returns 404, never leaks data.

### 3.9 Error codes

`PRODUCT_NOT_FOUND`, `WEIGHT_MISMATCH` (422), `ORDER_NOT_OPEN` (409), `ORDER_NOT_PAID`,
`EMPTY_ORDER`, `INVALID_STAFF_PIN` (403), `STAFF_REQUIRED` (403), `ITEM_NOT_FOUND`,
`VERIFICATION_NOT_FOUND`, `VERIFICATION_NOT_OVERRIDABLE`, `PAYMENT_IN_PROGRESS`, `PAYMENT_FAILED`
(402), `PAYMENT_NOT_PENDING` (409, cancelling a payment that already succeeded), `ORDER_NOT_FOUND`
(404, `POST events/` with an order of another store or an unknown order), `VALIDATION_ERROR`,
`AUTHENTICATION_FAILED`, `PERMISSION_DENIED`, `NOT_FOUND`.

### 3.10 Scale and kiosk simulator

Because the Android app does not exist yet, the repo ships a simulator that behaves like the kiosk:

- `SimulatedScale` – returns a weight for a product under a scenario, quantised to 5 g and seeded
  for reproducibility. Scenarios: `honest`, `swap` (expensive item placed for a cheap barcode),
  `double` (two units), `empty` (nothing on the scale), `drift` (`--drift-g`), `mixed`.
- `KioskClient` – thin HTTP client over `requests` that speaks the kiosk API exactly as the Android
  app will.
- `python manage.py seed_demo` – creates "Demo Mart" (USD, SLSH @ 8500), 6 categories and 17
  products with Somali names, admin `admin@demo.test / admin1234`, staff `S1` PIN `1234`, numeric
  kiosk staff `1001` PIN `1234` (Hodan Cabdi, `--kiosk-staff-code`), kiosk "Kiosk 1", and prints
  the kiosk token. Idempotent; each run rotates the kiosk token.
- `python manage.py simulate_checkout --scenario ... --items N [--override --staff-code S1 --pin 1234]
  [--pay WAAFI|EDAHAB --phone ...] [--abandon-after N | --idle-cancel]` – runs a complete checkout
  against a running server and prints each step. It sends `app_version="simulator"` and an all-OK
  device map in the heartbeat and posts `APP_STARTED` at the start. `--abandon-after N` polls the
  payment N times, cancels it and posts `PAYMENT_TIMEOUT` (exit 0 when the order is OPEN again;
  use a `...8888` phone). `--idle-cancel` skips payment, cancels with reason `IDLE_TIMEOUT` and no
  staff, and posts `IDLE_TIMEOUT` (exit 0 when the order is CANCELLED).

### 3.11 Kiosk app contract additions (2026-10-31)

Added for the Flutter kiosk app (`/home/eng-omar/posmobile`). All kiosk calls use
`Authorization: Kiosk <token>` and the standard error envelope.

**Heartbeat** – `POST /api/v1/kiosk/heartbeat/`. The body is now optional:

```json
{"app_version": "0.1.0", "devices": {"scanner": "OK", "scale": "WARN", "printer": "OK", "eas": "OFF"}}
```

Every key is optional; device values are `OK | WARN | FAULT | OFF`; an unknown device key or value
is 400 `VALIDATION_ERROR`. `app_version` and `devices` are stored on the Kiosk (visible read-only on
`admin/kiosks/`); an empty body only updates `last_seen_at`. The response `store` object gains
`secondary_currency` (`"SLSH"` or `null`) and `secondary_rate` (decimal string such as
`"8500.0000"` or `null`) next to `currency`, `tax_rate` and `default_weight_tolerance_g`.

**Staff verify** – `POST /api/v1/kiosk/staff/verify/` `{"staff_code": "1001", "pin": "1234"}` →
200 `{"id", "name", "role", "staff_code"}` (`name` = full name, else email). Wrong code/PIN,
inactive user or staff of another store → 403 `INVALID_STAFF_PIN`. Lets the kiosk open staff mode
before any action; the kiosk keeps the code + PIN in memory and still sends them with each
override / remove / cancel. The demo staff code `1001` is numeric for the kiosk keypad (`S1` is
kept for scripts).

**Kiosk events** – `POST /api/v1/kiosk/events/` `{"type": "STAFF_CALLED", "order": "<uuid>|null",
"detail": {}}` → 201 `{"id", "type", "order", "detail", "created_at"}`. Types: STAFF_CALLED,
UNKNOWN_BARCODE, PAYMENT_TIMEOUT, TERMINAL_LOCKED, TERMINAL_RELEASED, WEIGHT_RETRY_LIMIT,
IDLE_TIMEOUT, HARDWARE_FAULT, APP_STARTED. Bad type → 400; unknown or other-store order → 404
`ORDER_NOT_FOUND`. Distinct from `orders/{id}/events/` (RECEIPT_PRINTED / EAS_DEACTIVATED). Admins
read them at `GET /api/v1/admin/kiosk-events/` (store-scoped, paginated, filters `type`, `kiosk`,
`created_after`, `created_before`, extra field `kiosk_name`).

**Idle-timeout cancel** – `POST /api/v1/kiosk/orders/{id}/cancel/` with `{"reason": "IDLE_TIMEOUT"}`
cancels an OPEN basket even when it has items and no staff credentials are sent
(`cancel_reason = "IDLE_TIMEOUT"`). Any other reason on a basket with items still needs staff
(403 `STAFF_REQUIRED`).

**Cancel pending payment** – `POST /api/v1/kiosk/orders/{id}/payments/{pid}/cancel/` (no body) →
200 PaymentStatus. PENDING → `status: "FAILED"`, `failure_reason: "Abandoned by customer"`,
`order_status: "OPEN"` (the customer can pay again). Already FAILED → 200 unchanged (idempotent).
SUCCESS → 409 `PAYMENT_NOT_PENDING`. Payment of another store's order → 404.

**Product lookup** – `GET /api/v1/kiosk/products/lookup/?barcode=` adds `name_so` (string or
`null`) and `image_url` (absolute URL built from the request, or `null`).

**Admin catalog** – categories and products accept `name_so`; products accept a multipart `image`
upload and return a read-only absolute `image_url`. CSV import/export gains `name_so` as the last,
optional column (`...,category,name_so`). In dev (`DEBUG=True`) uploaded files are served from
`MEDIA_URL` (`/media/`); in production serve `MEDIA_ROOT` from the web server.

**Admin stores** – `secondary_currency` (3–4 letters, upper-cased, e.g. `SLSH`) and
`secondary_rate` (> 0, required when a code is set; clearing the code clears the rate). Demo store:
`SLSH` at `8500` per USD.

**Mock payment phones** – suffix `0000` → FAILED "Customer declined", `9999` → FAILED "TIMEOUT",
`8888` → stays PENDING (use with the kiosk payment timeout / cancel), anything else → SUCCESS.

---

## 4. Verification done

| Check | Result |
|---|---|
| `pytest` | **248 passed** (SQLite, 2026-10-31; was 184 at the initial commit) |
| `ruff check .` | clean |
| `python manage.py check` | clean |
| `makemigrations --check --dry-run` | no changes |
| OpenAPI generation (`spectacular --validate`) | 62 operations, 0 errors, 0 warnings |

Live end-to-end runs against `runserver` with the simulator (mock provider):

| Scenario | Outcome |
|---|---|
| honest, 4 items, Waafi | 4 PASS, payment SUCCESS, receipt #1 |
| swap, 3 items, override by S1, E-Dahab | 3 FAIL → 3 OVERRIDDEN, payment SUCCESS, receipt #2 |
| double and empty | all attempts REJECTED, order cancelled |
| mixed, 6 items, phone ending 0000 | payment FAILED "Customer declined", order reopened |
| wrong staff PIN | 403 `INVALID_STAFF_PIN`, simulator exits 1 |

Reports after those runs: sales-summary showed 2 paid orders; weight-incidents showed 18 attempts,
12 failed, 6 overridden.

Live runs on 2026-10-31 after the kiosk contract additions (fresh SQLite DB, `seed_demo`,
`runserver 127.0.0.1:8011`):

| Scenario | Outcome |
|---|---|
| honest, 2 items, Waafi `252611111111` | 2 PASS, payment SUCCESS, receipt #1 |
| swap, 1 item, override by `1001`/`1234`, E-Dahab `252651111111` | FAIL → OVERRIDDEN, SUCCESS, receipt #2 |
| Waafi `252618888888`, `--abandon-after 2` | 2 polls PENDING, cancel → FAILED "Abandoned by customer", order OPEN, `PAYMENT_TIMEOUT` event |
| `--idle-cancel` | order CANCELLED with `cancel_reason=IDLE_TIMEOUT`, no staff, `IDLE_TIMEOUT` event |

Every run posted `APP_STARTED` and the heartbeat stored `app_version="simulator"` and the all-OK
device map.

---

## 5. Git

- Repository: https://github.com/Omar-Cj/FastPos.git
- Branch `main`, tracking `origin/main`, pushed.
- Commit `243eb64` "Initial FastPOS MVP backend (Django + DRF)".
- The project summary `.docx` and the hardware photo `.jpeg` are intentionally **not** committed.
- `.gitignore` excludes `.venv/`, `.env`, `*.sqlite3`, caches, `staticfiles/`, `media/`.

---

## 6. Local environment notes

- WSL2, Python 3.12.3, virtualenv at `.venv`, dependencies installed with pip.
- MySQL 8.0.46 configured 2026-10-31: `.env` (git-ignored) points `DATABASE_URL` at
  `127.0.0.1:33061` (this install's non-default port) with the root login; migrations, `seed_demo`
  and the full suite (249 passed) run on MySQL. The MySQL time zone tables had to be loaded once
  (`mysql_tzinfo_to_sql /usr/share/zoneinfo | mysql ... mysql`); without them `TruncDate` yields
  NULL and the per-day sales report 500s. Documented in README > MySQL. SQLite remains the
  fallback when `DATABASE_URL` is unset.
- No Docker or Redis on this machine. Celery runs eagerly. `docker-compose.yml` is for other
  machines and production.

---

## 7. Open items

### Blocked on external input
- [x] **MySQL credentials** – done 2026-10-31 (root login in `.env`, port 33061, tz tables loaded,
  249 tests green on MySQL). Production should use a dedicated user (`scripts/mysql_setup.sql`).
- [ ] **Waafi merchant docs + credentials** – confirm request/response field names in
  `apps/payments/providers/waafi.py`, fill `WAAFI_*` env vars, test in sandbox.
- [ ] **E-Dahab merchant docs + credentials** – same for `apps/payments/providers/edahab.py` and
  `EDAHAB_*`.
- [ ] **Sample Android app API contract** – when available, compare with `docs/openapi.yaml` and
  adjust field names if the other developer already has a contract.

### Suggested backend improvements (not started)
- [ ] Add a `store` FK on `WeightVerification` to simplify reporting queries.
- [ ] Record `removed_by` on item removal for a complete audit trail.
- [ ] Give `Kiosk.key_hash` a safe default so a kiosk can be created in Django admin without a token.
- [ ] Provider webhooks / callbacks once the merchant APIs confirm they exist (polling works today).
- [ ] Receipt template for the 80 mm printer (currently the kiosk receives structured JSON).

### Kiosk client
- The Flutter kiosk app at `/home/eng-omar/posmobile` is now the kiosk client (entrypoint
  `lib/main_kiosk.dart`) and targets the contract in sections 3.7 and 3.11. The Android APK must be
  built on a machine with the Android SDK (not available in this WSL2 environment).

### Hand-over for other developers
- **Android kiosk dev** – implement against the kiosk API in sections 3.7 and 3.11 using
  `docs/openapi.yaml`; `apps/simulator/kiosk_client.py` is a working reference client.
- **React admin dev** – implement against the admin API in section 3.8; Swagger UI at `/api/docs/`.
  Login with `admin@demo.test / admin1234` after `seed_demo`.

---

## 8. Timeline

| Date | Milestone |
|---|---|
| 2026-10 | Requirements clarified: server-side weight check, Waafi + E-Dahab, fixed-weight catalog |
| 2026-10 | Plan approved; revised to MySQL + React admin |
| 2026-10 | Entity and weight-detection walkthrough delivered |
| 2026-10 | Full MVP implemented with parallel agents (foundation, orders, payments, stores/accounts/catalog/reports/simulator) |
| 2026-10 | 184 tests passing, live simulator runs verified |
| 2026-10-31 | Initial commit pushed to GitHub `main`; this document written |
| 2026-10-31 | Kiosk app contract additions (name_so, product image, SLSH, heartbeat device status, staff verify, kiosk events, idle cancel, payment cancel); simulator, seed and OpenAPI updated; 248 tests passing |

---

## 9. Kiosk app E2E results (2026-10-31)

Wave 3 of the kiosk integration plan. The production Flutter kiosk code (`KioskController` +
`DioKioskApi` + simulated scanner/scale/printer/EAS, `/home/eng-omar/posmobile`) was driven against
this backend running on a scratch SQLite DB, in real time, with server state asserted through the
admin API. Test file: `posmobile/test_e2e/kiosk_live_e2e_test.dart` (outside `test/`, skipped
without the env vars). Timings used: idle 4 s, payment timeout 8 s, poll 1 s, success hold 1 s.
Three consecutive full runs passed (about 53 s each).

| # | Scenario | Driven as | Asserted | Result |
|---|---|---|---|---|
| 1 | Honest | UHT Milk @ 1035 g, Sugar @ 1010 g, SIM card (no weigh); Waafi `631111111` | `paymentSuccess`, receipt number, one 48-column receipt with `DEMO MART`, `TOTAL USD`, `TOTAL SLSH` and Somali names, 1 EAS pulse; admin order `PAID`, `receipt_number`, `receipt_printed_at`, `eas_deactivated_at`, 3 items | Pass |
| 2 | Swap + override | Perfume @ 1020 g, retry ×2 (scale auto-cycles), staff `1001`/`1234`, approve override, pay | locked `weightRetries`; overridden line; admin verifications FAIL ×3, outcomes REJECTED, REJECTED, OVERRIDDEN; `WEIGHT_RETRY_LIMIT`, `TERMINAL_LOCKED`, `TERMINAL_RELEASED {override: true}` | Pass |
| 3 | Double | Water @ 1040 g → mismatch; retry, lift, place 520 g | verified, not overridden; admin verifications FAIL then PASS (520 g) | Pass |
| 4 | Declined | `630000000` → failed; retry with `631111111` | `failureReason` "Customer declined"; admin payments FAILED then SUCCESS; order `PAID` | Pass |
| 5 | Payment timeout | `638888888`, wait out the 8 s countdown; then `639999999` | ~8 s → `paymentFailed` "timed out"; admin payment FAILED "Abandoned by customer", order `OPEN`, `PAYMENT_TIMEOUT` event with `payment_id`; `9999` fails in < 3 s with "TIMEOUT" | Pass |
| 6 | Idle timeout | one item, then wait | back to `idle`; admin order `CANCELLED` / `IDLE_TIMEOUT`; `IDLE_TIMEOUT` event (`item_count` 1) | Pass |
| 7 | Reprint | sale, then staff sign-in after the reset → reprint | second document, identical text | Pass |
| 8 | Incident events | Help (customer lock) → staff release; scan `0000000000000` → staff release | `STAFF_CALLED`, `UNKNOWN_BARCODE {barcode}`, 2× `TERMINAL_RELEASED`, all with `kiosk_name` "Kiosk 1" | Pass |
| 9 | Heartbeat | `HeartbeatService` every 1 s, printer paper low | admin kiosk `device_status` `{scanner OK, scale OK, printer WARN, eas OK}`, `app_version "e2e"`, online within 3 s | Pass |
| 10 | Lookup | `lookupProduct(6291041500220)`, unknown barcode | `name_so` "Sonkor 1kg", `image_url` null, tolerance 20; `PRODUCT_NOT_FOUND` | Pass |

**Defects found and fixed**

- `config/settings/base.py`: on SQLite (the dev default) the second of the two back-to-back
  order events (`RECEIPT_PRINTED`, `EAS_DEACTIVATED`) failed with 500 "database is locked".
  `select_for_update()` is a no-op on SQLite, so the deferred transaction's read-then-write upgrade
  failed straight away. SQLite now uses `transaction_mode: IMMEDIATE` and `timeout: 20` (MySQL is
  unchanged).
- `config/settings/base.py`: the CORS preflight rejected the kiosk's `Idempotency-Key` header, so
  the Flutter **web** kiosk could not add any item or start a payment. Added
  `CORS_ALLOW_HEADERS = (*default_headers, "idempotency-key")`. Regression test:
  `apps/core/tests/test_foundation.py::test_cors_preflight_allows_idempotency_key` (249 tests now).
- `posmobile/lib/kiosk_app.dart`: the real entrypoint rendered every kiosk `Text` with Flutter's
  yellow-underlined fallback style because `KioskScreen` had no `Material` ancestor (the design
  harness supplies a `Scaffold`). The running kiosk is now wrapped in a `Material`. The existing
  `test/kiosk_app_test.dart` boot test now also checks for that ancestor.

**Browser smoke** (`flutter build web -t lib/main_kiosk.dart --dart-define=KIOSK_SIMULATE=true`,
served on :7357, Playwright Chromium at 1280×800, Flutter semantics enabled through
`flt-semantics-placeholder`). Pairing form → *Test connection* showed "Connected to Demo Mart ·
Kiosk 1" → *Save and start* → attract screen "Scan an item or tap to start". After a reload the
stored pairing booted straight to the attract screen. SIM panel: scanned `6291041500268`, placed
1035 g, and the basket showed "UHT Milk 1L, 1 × $1.45, 12,325 SL", total $1.45 (after the CORS fix;
before it, the console showed the blocked preflight). Cosmetic nits seen: "1 items" badge, and the
SIM chip overlaps the secondary-currency line under the total.

**Reproduce**

```bash
# backend (from /home/eng-omar/fastpos), scratch DB so db.sqlite3 is untouched
export DATABASE_URL=sqlite:////tmp/fastpos-e2e.sqlite3
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_demo          # note KIOSK_TOKEN=... (new token on every run)
.venv/bin/python manage.py runserver 0.0.0.0:8000

# kiosk (from /home/eng-omar/posmobile)
KIOSK_E2E_BASE_URL=http://127.0.0.1:8000 KIOSK_E2E_TOKEN=<token> \
  flutter test test_e2e/kiosk_live_e2e_test.dart

# browser smoke
flutter build web -t lib/main_kiosk.dart --dart-define=KIOSK_SIMULATE=true
/home/eng-omar/fastpos/.venv/bin/python -m http.server 7357 --directory /home/eng-omar/posmobile/build/web
# open http://127.0.0.1:7357/, pair with http://127.0.0.1:8000 + the token
```
