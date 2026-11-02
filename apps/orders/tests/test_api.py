"""Kiosk + admin HTTP contract for orders."""

import uuid
from decimal import Decimal

import pytest

from apps.core.exceptions import WeightMismatch
from apps.orders import services
from apps.orders.models import KioskEvent, Order, WeightVerification

pytestmark = pytest.mark.django_db

K = "/api/v1/kiosk/orders/"
A = "/api/v1/admin/"
E = "/api/v1/kiosk/events/"


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

    def test_idle_timeout_cancel_with_items_without_staff(self, kiosk_client, order_id, product):
        kiosk_client.post(_items(order_id), {"barcode": product.barcode, "measured_weight_g": 500})
        r = kiosk_client.post(f"{K}{order_id}/cancel/", {"reason": "left"})
        assert r.status_code == 403 and r.json()["error"]["code"] == "STAFF_REQUIRED"
        r = kiosk_client.post(f"{K}{order_id}/cancel/", {"reason": "IDLE_TIMEOUT"})
        assert r.status_code == 200
        assert r.json()["status"] == "CANCELLED" and r.json()["cancel_reason"] == "IDLE_TIMEOUT"


class TestKioskEvents:
    def test_create_with_order(self, kiosk_client, order_id, kiosk):
        r = kiosk_client.post(E, {"type": "STAFF_CALLED", "order": order_id, "detail": {"stage": "scan"}},
                              format="json")
        assert r.status_code == 201
        body = r.json()
        assert set(body) == {"id", "type", "order", "detail", "created_at"}
        assert (body["type"], body["order"], body["detail"]) == ("STAFF_CALLED", order_id, {"stage": "scan"})
        ev = KioskEvent.objects.get(pk=body["id"])
        assert ev.kiosk_id == kiosk.id and ev.store_id == kiosk.store_id

    def test_create_without_order(self, kiosk_client):
        r = kiosk_client.post(E, {"type": "APP_STARTED"}, format="json")
        assert r.status_code == 201 and r.json()["order"] is None and r.json()["detail"] == {}
        r = kiosk_client.post(E, {"type": "HARDWARE_FAULT", "order": None, "detail": {"device": "scale"}},
                              format="json")
        assert r.status_code == 201 and r.json()["order"] is None

    def test_other_store_or_unknown_order_is_404(self, kiosk_client, other_kiosk_client):
        foreign = other_kiosk_client.post(K).json()["id"]
        for oid in (foreign, str(uuid.uuid4())):
            r = kiosk_client.post(E, {"type": "STAFF_CALLED", "order": oid}, format="json")
            assert r.status_code == 404
        assert not KioskEvent.objects.exists()

    def test_bad_type_is_400(self, kiosk_client):
        r = kiosk_client.post(E, {"type": "NOPE"}, format="json")
        assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_requires_kiosk_auth(self, anon_client, admin_client):
        assert anon_client.post(E, {"type": "APP_STARTED"}, format="json").status_code == 401
        assert admin_client.post(E, {"type": "APP_STARTED"}, format="json").status_code in (401, 403)

    def test_does_not_clash_with_order_events_route(self, kiosk_client, order_id):
        from django.urls import resolve

        assert resolve(E).url_name == "kiosk-event-create"
        assert resolve(f"{K}{order_id}/events/").url_name == "kiosk-order-events"


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


class TestAdminKioskEvents:
    @pytest.fixture
    def events(self, kiosk, store, other_store, make_kiosk):
        k2 = make_kiosk(store, "Kiosk 2", "SN-K2")
        foreign_kiosk = make_kiosk(other_store, "Foreign", "SN-F")
        e1 = services.record_kiosk_event(kiosk, KioskEvent.Type.STAFF_CALLED)
        e2 = services.record_kiosk_event(kiosk, KioskEvent.Type.IDLE_TIMEOUT)
        e3 = services.record_kiosk_event(k2, KioskEvent.Type.STAFF_CALLED)
        services.record_kiosk_event(foreign_kiosk, KioskEvent.Type.STAFF_CALLED)
        return e1, e2, e3, k2

    def test_list_is_scoped_and_ordered(self, admin_client, other_admin_client, events):
        e1, e2, e3, _ = events
        r = admin_client.get(f"{A}kiosk-events/")
        assert r.status_code == 200 and r.json()["count"] == 3
        rows = r.json()["results"]
        assert [x["id"] for x in rows] == [e3.id, e2.id, e1.id]
        assert {x["kiosk_name"] for x in rows} == {"Kiosk 1", "Kiosk 2"}
        assert other_admin_client.get(f"{A}kiosk-events/").json()["count"] == 1
        assert other_admin_client.get(f"{A}kiosk-events/{e1.id}/").status_code == 404

    def test_filters(self, admin_client, events):
        _, _, _, k2 = events
        assert admin_client.get(f"{A}kiosk-events/?type=STAFF_CALLED").json()["count"] == 2
        assert admin_client.get(f"{A}kiosk-events/?type=IDLE_TIMEOUT").json()["count"] == 1
        assert admin_client.get(f"{A}kiosk-events/?kiosk={k2.id}").json()["count"] == 1
        assert admin_client.get(f"{A}kiosk-events/?type=BOGUS").status_code == 400
        assert admin_client.get(f"{A}kiosk-events/?created_after=2000-01-01T00:00:00Z").json()["count"] == 3
        assert admin_client.get(f"{A}kiosk-events/?created_before=2000-01-01T00:00:00Z").json()["count"] == 0

    def test_read_only(self, admin_client, events):
        assert admin_client.post(f"{A}kiosk-events/", {"type": "STAFF_CALLED"}).status_code == 405

    def test_kiosk_cannot_read_admin_list(self, kiosk_client, events):
        assert kiosk_client.get(f"{A}kiosk-events/").status_code in (401, 403)
