import uuid

import pytest

from apps.orders.models import Order
from apps.payments.models import Payment
from apps.stores.models import Kiosk

from .helpers import order_with_items

K = "/api/v1/kiosk"
A = "/api/v1/admin"


@pytest.fixture
def order(kiosk, product):
    return order_with_items(kiosk, product)


@pytest.fixture
def pay(kiosk_client, django_capture_on_commit_callbacks):
    def _pay(order, method="WAAFI", phone="252615000001", **headers):
        with django_capture_on_commit_callbacks(execute=True):
            return kiosk_client.post(f"{K}/orders/{order.id}/payments/",
                                     {"method": method, "customer_phone": phone}, headers=headers)
    return _pay


class TestInitiate:
    def test_202_and_replay_200(self, pay, order):
        key = str(uuid.uuid4())
        r = pay(order, **{"Idempotency-Key": key})
        assert r.status_code == 202, r.json()
        body = r.json()
        assert set(body) == {"id", "order", "method", "amount", "currency", "customer_phone", "status",
                             "failure_reason", "provider_reference", "initiated_at", "completed_at"}
        # The response is built before the on_commit task runs, so it is still PENDING; poll for the result.
        assert body["status"] == "PENDING" and body["amount"] == "2.50"
        assert Payment.objects.get(pk=body["id"]).status == Payment.Status.SUCCESS

        r2 = pay(order, **{"Idempotency-Key": key})
        assert r2.status_code == 200 and r2.json()["id"] == body["id"]
        assert Payment.objects.count() == 1

    def test_failed_then_retry(self, pay, order):
        r = pay(order, phone="+252610000000")
        assert r.status_code == 202
        assert Payment.objects.get(pk=r.json()["id"]).status == Payment.Status.FAILED
        order.refresh_from_db()
        assert order.status == Order.Status.OPEN
        r2 = pay(order, method="EDAHAB")
        assert Payment.objects.get(pk=r2.json()["id"]).status == Payment.Status.SUCCESS

    @pytest.mark.parametrize("payload", [
        {"method": "WAAFI", "customer_phone": "12ab"},
        {"method": "WAAFI", "customer_phone": "123"},
        {"method": "PAYPAL", "customer_phone": "252615000001"},
    ])
    def test_validation(self, kiosk_client, order, payload):
        r = kiosk_client.post(f"{K}/orders/{order.id}/payments/", payload)
        assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_bad_idempotency_key(self, pay, order):
        assert pay(order, **{"Idempotency-Key": "nope"}).status_code == 400

    def test_in_progress_409(self, kiosk_client, order, django_capture_on_commit_callbacks):
        url = f"{K}/orders/{order.id}/payments/"
        body = {"method": "WAAFI", "customer_phone": "252615000001"}
        with django_capture_on_commit_callbacks(execute=False):
            assert kiosk_client.post(url, body).status_code == 202
        r = kiosk_client.post(url, body)
        assert r.status_code == 409 and r.json()["error"]["code"] == "PAYMENT_IN_PROGRESS"

    def test_paid_order_409(self, pay, order):
        pay(order)
        r = pay(order)
        assert r.status_code == 409 and r.json()["error"]["code"] == "ORDER_NOT_OPEN"

    def test_empty_order(self, pay, kiosk):
        r = pay(Order.objects.create(store=kiosk.store, kiosk=kiosk))
        assert r.status_code == 400 and r.json()["error"]["code"] == "EMPTY_ORDER"

    def test_other_store_order_404(self, pay, other_store, make_product):
        k = Kiosk(store=other_store, name="K2", serial_number="SN-2")
        k.issue_token()
        k.save()
        other = order_with_items(k, make_product(barcode="9", store_=other_store))
        assert pay(other).status_code == 404


class TestPoll:
    def test_detail_includes_order_status(self, pay, kiosk_client, order):
        pid = pay(order).json()["id"]
        r = kiosk_client.get(f"{K}/orders/{order.id}/payments/{pid}/")
        assert r.status_code == 200
        assert r.json()["status"] == "SUCCESS" and r.json()["order_status"] == "PAID"

    def test_payment_of_other_order_404(self, pay, kiosk_client, kiosk, product, order):
        pid = pay(order).json()["id"]
        other = order_with_items(kiosk, product)
        assert kiosk_client.get(f"{K}/orders/{other.id}/payments/{pid}/").status_code == 404


class TestReceiptAndEvents:
    def test_receipt_groups_identical_products(self, pay, kiosk_client, kiosk, product):
        order = order_with_items(kiosk, product, product)
        pay(order)
        r = kiosk_client.get(f"{K}/orders/{order.id}/receipt/")
        assert r.status_code == 200
        body = r.json()
        assert body["receipt_number"] == 1 and body["item_count"] == 2
        assert body["lines"] == [{"barcode": product.barcode, "name": product.name, "quantity": 2,
                                  "unit_price": "2.50", "line_total": "5.00"}]
        assert body["payment"]["method"] == "WAAFI"

    def test_receipt_unpaid_409(self, kiosk_client, order):
        r = kiosk_client.get(f"{K}/orders/{order.id}/receipt/")
        assert r.status_code == 409 and r.json()["error"]["code"] == "ORDER_NOT_PAID"

    def test_events_idempotent(self, pay, kiosk_client, order):
        pay(order)
        url = f"{K}/orders/{order.id}/events/"
        r1 = kiosk_client.post(url, {"type": "RECEIPT_PRINTED"})
        r2 = kiosk_client.post(url, {"type": "RECEIPT_PRINTED"})
        assert r1.status_code == r2.status_code == 200
        assert r1.json()["recorded_at"] == r2.json()["recorded_at"]
        assert r1.json()["order_id"] == str(order.id) and r1.json()["type"] == "RECEIPT_PRINTED"
        assert kiosk_client.post(url, {"type": "EAS_DEACTIVATED"}).status_code == 200
        order.refresh_from_db()
        assert order.receipt_printed_at and order.eas_deactivated_at

    def test_events_require_paid(self, kiosk_client, order):
        r = kiosk_client.post(f"{K}/orders/{order.id}/events/", {"type": "EAS_DEACTIVATED"})
        assert r.status_code == 409 and r.json()["error"]["code"] == "ORDER_NOT_PAID"

    def test_event_type_validated(self, pay, kiosk_client, order):
        pay(order)
        assert kiosk_client.post(f"{K}/orders/{order.id}/events/", {"type": "DANCE"}).status_code == 400

    def test_admin_jwt_rejected_on_kiosk_endpoint(self, admin_client, order):
        assert admin_client.get(f"{K}/orders/{order.id}/receipt/").status_code in (401, 403)


class TestAdmin:
    def test_list_scoped_and_filtered(self, pay, admin_client, other_admin_client, kiosk, product, order):
        pay(order)
        pay(order_with_items(kiosk, product), phone="252610000000")

        r = admin_client.get(f"{A}/payments/")
        assert r.status_code == 200
        results = r.json()["results"]
        assert len(results) == 2 and "order_receipt_number" in results[0]
        assert other_admin_client.get(f"{A}/payments/").json()["results"] == []

        failed = admin_client.get(f"{A}/payments/", {"status": "FAILED"}).json()["results"]
        assert len(failed) == 1 and failed[0]["order_receipt_number"] is None
        assert len(admin_client.get(f"{A}/payments/", {"search": "252610000000"}).json()["results"]) == 1
        assert admin_client.get(f"{A}/payments/", {"method": "EDAHAB"}).json()["results"] == []
        assert len(admin_client.get(f"{A}/payments/", {"initiated_after": "2000-01-01T00:00:00Z"})
                   .json()["results"]) == 2
        before = admin_client.get(f"{A}/payments/", {"initiated_before": "2000-01-01T00:00:00Z"})
        assert before.json()["results"] == []

    def test_detail_and_read_only(self, pay, admin_client, other_admin_client, order):
        pid = pay(order).json()["id"]
        assert admin_client.get(f"{A}/payments/{pid}/").json()["order_receipt_number"] == 1
        assert other_admin_client.get(f"{A}/payments/{pid}/").status_code == 404
        assert admin_client.post(f"{A}/payments/", {}).status_code == 405

    def test_kiosk_cannot_list(self, kiosk_client):
        assert kiosk_client.get(f"{A}/payments/").status_code in (401, 403)
