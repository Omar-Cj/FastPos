"""Kiosk + admin HTTP contract for orders."""

import uuid
from decimal import Decimal

import pytest

from apps.core.exceptions import WeightMismatch
from apps.orders import services
from apps.orders.models import Order, WeightVerification

pytestmark = pytest.mark.django_db

K = "/api/v1/kiosk/orders/"
A = "/api/v1/admin/"


def _items(order_id):
    return f"{K}{order_id}/items/"


@pytest.fixture
def order_id(kiosk_client):
    r = kiosk_client.post(K)
    assert r.status_code == 201
    return r.json()["id"]


class TestKioskOrders:
    def test_create_and_get(self, kiosk_client, order_id, kiosk):
        r = kiosk_client.get(f"{K}{order_id}/")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "OPEN" and body["items"] == [] and body["kiosk"] == kiosk.id
        assert body["total"] == "0.00" and body["receipt_number"] is None

    def test_other_store_kiosk_gets_404(self, other_kiosk_client, order_id):
        r = other_kiosk_client.get(f"{K}{order_id}/")
        assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
        r = other_kiosk_client.post(_items(order_id), {"barcode": "x", "measured_weight_g": 1})
        assert r.status_code == 404

    def test_admin_jwt_rejected(self, admin_client):
        assert admin_client.post(K).status_code in (401, 403)


class TestKioskAddItem:
    def test_add_pass(self, kiosk_client, order_id, product):
        r = kiosk_client.post(_items(order_id), {"barcode": product.barcode, "measured_weight_g": 505})
        assert r.status_code == 201
        body = r.json()
        assert body["item"]["weight_status"] == "PASSED"
        assert body["item"]["product_id"] == product.id and body["item"]["unit_price"] == "2.50"
        assert body["order"] == {"subtotal": "2.50", "tax": "0.00", "total": "2.50", "item_count": 1}

    def test_weight_mismatch_envelope(self, kiosk_client, order_id, product):
        r = kiosk_client.post(_items(order_id), {"barcode": product.barcode, "measured_weight_g": 600})
        assert r.status_code == 422
        err = r.json()["error"]
        v = WeightVerification.objects.get()
        assert err["code"] == "WEIGHT_MISMATCH"
        assert err["details"] == {"verification_id": v.id, "expected_weight_g": 500, "measured_weight_g": 600,
                                  "tolerance_g": 10, "delta_g": 100}

    def test_product_not_found(self, kiosk_client, order_id):
        r = kiosk_client.post(_items(order_id), {"barcode": "NOPE", "measured_weight_g": 5})
        assert r.status_code == 404 and r.json()["error"]["code"] == "PRODUCT_NOT_FOUND"

    def test_validation_error(self, kiosk_client, order_id):
        r = kiosk_client.post(_items(order_id), {"measured_weight_g": -1})
        assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_idempotent_replay_returns_200(self, kiosk_client, order_id, product):
        key = str(uuid.uuid4())
        data = {"barcode": product.barcode, "measured_weight_g": 500}
        r1 = kiosk_client.post(_items(order_id), data, HTTP_IDEMPOTENCY_KEY=key)
        r2 = kiosk_client.post(_items(order_id), data, HTTP_IDEMPOTENCY_KEY=key)
        assert (r1.status_code, r2.status_code) == (201, 200)
        assert r1.json()["item"]["id"] == r2.json()["item"]["id"]
        assert r2.json()["order"]["item_count"] == 1

    def test_bad_idempotency_key(self, kiosk_client, order_id, product):
        r = kiosk_client.post(_items(order_id), {"barcode": product.barcode, "measured_weight_g": 500},
                              HTTP_IDEMPOTENCY_KEY="not-a-uuid")
        assert r.status_code == 400

    def test_order_not_open(self, kiosk_client, order_id, product):
        kiosk_client.post(_items(order_id), {"barcode": product.barcode, "measured_weight_g": 500})
        services.set_awaiting_payment(Order.objects.get(pk=order_id))
        r = kiosk_client.post(_items(order_id), {"barcode": product.barcode, "measured_weight_g": 500})
        assert r.status_code == 409 and r.json()["error"]["code"] == "ORDER_NOT_OPEN"


class TestKioskStaffActions:
    def _reject(self, kiosk_client, order_id, product):
        r = kiosk_client.post(_items(order_id), {"barcode": product.barcode, "measured_weight_g": 600})
        return r.json()["error"]["details"]["verification_id"]

    def test_override(self, kiosk_client, order_id, product, staff_user):
        vid = self._reject(kiosk_client, order_id, product)
        r = kiosk_client.post(f"{_items(order_id)}override/",
                              {"verification_id": vid, "staff_code": "S1", "pin": "1234", "reason": "bag"})
        assert r.status_code == 201
        assert r.json()["item"]["weight_status"] == "OVERRIDDEN" and r.json()["order"]["item_count"] == 1

    def test_override_bad_pin(self, kiosk_client, order_id, product, staff_user):
        vid = self._reject(kiosk_client, order_id, product)
        r = kiosk_client.post(f"{_items(order_id)}override/",
                              {"verification_id": vid, "staff_code": "S1", "pin": "0000"})
        assert r.status_code == 403 and r.json()["error"]["code"] == "INVALID_STAFF_PIN"
        assert WeightVerification.objects.get(pk=vid).outcome == "REJECTED"

    def test_remove_item(self, kiosk_client, order_id, product, staff_user):
        item_id = kiosk_client.post(_items(order_id), {"barcode": product.barcode, "measured_weight_g": 500}
                                    ).json()["item"]["id"]
        bad = kiosk_client.delete(f"{_items(order_id)}{item_id}/", {"staff_code": "S1", "pin": "9"})
        assert bad.status_code == 403 and bad.json()["error"]["code"] == "INVALID_STAFF_PIN"
        r = kiosk_client.delete(f"{_items(order_id)}{item_id}/", {"staff_code": "S1", "pin": "1234"})
        assert r.status_code == 200
        assert r.json() == {"order": {"subtotal": "0.00", "tax": "0.00", "total": "0.00", "item_count": 0}}

    def test_cancel_empty_without_staff(self, kiosk_client, order_id):
        r = kiosk_client.post(f"{K}{order_id}/cancel/", {"reason": "left"})
        assert r.status_code == 200 and r.json()["status"] == "CANCELLED"

    def test_cancel_with_items(self, kiosk_client, order_id, product, staff_user):
        kiosk_client.post(_items(order_id), {"barcode": product.barcode, "measured_weight_g": 500})
        r = kiosk_client.post(f"{K}{order_id}/cancel/", {})
        assert r.status_code == 403 and r.json()["error"]["code"] == "STAFF_REQUIRED"
        r = kiosk_client.post(f"{K}{order_id}/cancel/", {"staff_code": "S1", "pin": "nope"})
        assert r.json()["error"]["code"] == "INVALID_STAFF_PIN"
        r = kiosk_client.post(f"{K}{order_id}/cancel/", {"staff_code": "S1", "pin": "1234", "reason": "x"})
        assert r.status_code == 200 and r.json()["cancel_reason"] == "x"


@pytest.fixture
def paid_setup(kiosk, product, store):
    store.tax_rate = Decimal("0.05")
    store.save()
    o1 = services.open_order(kiosk)
    services.add_item(o1, kiosk, product.barcode, 500)
    with pytest.raises(WeightMismatch):
        services.add_item(o1, kiosk, product.barcode, 700)
    o2 = services.open_order(kiosk)
    return o1, o2


class TestAdmin:
    def test_list_and_filter(self, admin_client, paid_setup):
        o1, o2 = paid_setup
        r = admin_client.get(f"{A}orders/")
        assert r.status_code == 200 and r.json()["count"] == 2
        row = next(x for x in r.json()["results"] if x["id"] == str(o1.id))
        assert row["item_count"] == 1 and row["kiosk_name"] == "Kiosk 1" and row["total"] == "2.63"
        ids = [x["id"] for x in admin_client.get(f"{A}orders/?ordering=-total").json()["results"]]
        assert ids == [str(o1.id), str(o2.id)]
        assert admin_client.get(f"{A}orders/?status=PAID").json()["count"] == 0
        assert admin_client.get(f"{A}orders/?created_after=2000-01-01T00:00:00Z").json()["count"] == 2
        assert admin_client.get(f"{A}orders/?created_before=2000-01-01T00:00:00Z").json()["count"] == 0

    def test_detail_nests_items_verifications_payments(self, admin_client, paid_setup):
        o1, _ = paid_setup
        body = admin_client.get(f"{A}orders/{o1.id}/").json()
        assert len(body["items"]) == 1 and len(body["verifications"]) == 2 and body["payments"] == []

    def test_weight_verifications_filter(self, admin_client, paid_setup):
        r = admin_client.get(f"{A}weight-verifications/?result=FAIL")
        assert r.status_code == 200 and r.json()["count"] == 1
        row = r.json()["results"][0]
        assert row["outcome"] == "REJECTED" and row["delta_g"] == 200 and row["barcode"]

    def test_other_store_admin_sees_nothing(self, other_admin_client, paid_setup):
        o1, _ = paid_setup
        assert other_admin_client.get(f"{A}orders/").json()["count"] == 0
        assert other_admin_client.get(f"{A}weight-verifications/").json()["count"] == 0
        assert other_admin_client.get(f"{A}orders/{o1.id}/").status_code == 404

    def test_superuser_needs_store_param(self, superuser_client, paid_setup, store):
        assert superuser_client.get(f"{A}orders/").status_code == 400
        assert superuser_client.get(f"{A}orders/?store={store.id}").json()["count"] == 2

    def test_kiosk_cannot_read_admin(self, kiosk_client):
        assert kiosk_client.get(f"{A}orders/").status_code in (401, 403)
