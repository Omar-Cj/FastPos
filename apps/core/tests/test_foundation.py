"""Smoke tests for the shared foundation: kiosk auth, JWT auth, error envelope, receipt numbering."""

import pytest
from django.urls import path
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import WeightMismatch
from apps.core.mixins import AdminViewMixin, KioskViewMixin
from apps.orders import services
from apps.orders.models import Order, OrderItem
from apps.stores.models import Kiosk


class _KioskPing(KioskViewMixin, APIView):
    def get(self, request):
        return Response({"kiosk": self.kiosk.id, "store": self.store.id})


class _AdminPing(AdminViewMixin, APIView):
    def get(self, request):
        return Response({"user": request.user.email})


class _Boom(KioskViewMixin, APIView):
    def get(self, request):
        raise WeightMismatch(details={"verification_id": 7})


urlpatterns = [
    path("t/kiosk/", _KioskPing.as_view()),
    path("t/admin/", _AdminPing.as_view()),
    path("t/boom/", _Boom.as_view()),
]


@pytest.mark.urls(__name__)
class TestAuth:
    def test_kiosk_token_authenticates(self, kiosk_client, kiosk):
        r = kiosk_client.get("/t/kiosk/")
        assert r.status_code == 200
        assert r.json() == {"kiosk": kiosk.id, "store": kiosk.store_id}

    def test_bad_kiosk_token_rejected(self, db, anon_client):
        anon_client.credentials(HTTP_AUTHORIZATION="Kiosk nope")
        r = anon_client.get("/t/kiosk/")
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "AUTHENTICATION_FAILED"

    def test_inactive_kiosk_rejected(self, kiosk_client, kiosk):
        Kiosk.objects.filter(pk=kiosk.pk).update(is_active=False)
        assert kiosk_client.get("/t/kiosk/").status_code == 401

    def test_jwt_cannot_call_kiosk_endpoint(self, admin_client):
        r = admin_client.get("/t/kiosk/")
        assert r.status_code in (401, 403)

    def test_admin_jwt_authenticates(self, admin_client, admin_user):
        r = admin_client.get("/t/admin/")
        assert r.status_code == 200
        assert r.json()["user"] == admin_user.email

    def test_staff_role_cannot_call_admin_endpoint(self, staff_client):
        assert staff_client.get("/t/admin/").status_code == 403

    def test_kiosk_cannot_call_admin_endpoint(self, kiosk_client):
        assert kiosk_client.get("/t/admin/").status_code in (401, 403)

    def test_error_envelope(self, kiosk_client):
        r = kiosk_client.get("/t/boom/")
        assert r.status_code == 422
        assert r.json() == {
            "error": {
                "code": "WEIGHT_MISMATCH",
                "message": "Measured weight does not match the expected weight.",
                "details": {"verification_id": 7},
            }
        }


class TestStaffPin:
    def test_verify_staff(self, staff_user, store):
        from apps.accounts.services import verify_staff
        from apps.core.exceptions import InvalidStaffPin

        assert verify_staff(store, "S1", "1234") == staff_user
        with pytest.raises(InvalidStaffPin):
            verify_staff(store, "S1", "9999")
        with pytest.raises(InvalidStaffPin):
            verify_staff(store, "S2", "1234")

    def test_staff_scoped_to_store(self, staff_user, other_store):
        from apps.accounts.services import verify_staff
        from apps.core.exceptions import InvalidStaffPin

        with pytest.raises(InvalidStaffPin):
            verify_staff(other_store, "S1", "1234")


def _order_with_item(kiosk, product):
    order = Order.objects.create(store=kiosk.store, kiosk=kiosk)
    OrderItem.objects.create(
        order=order, product=product, barcode=product.barcode, product_name=product.name,
        unit_price=product.price, expected_weight_g=product.expected_weight_g, tolerance_g=10,
        measured_weight_g=product.expected_weight_g, weight_status=OrderItem.WeightStatus.PASSED,
    )
    return order


class TestLifecycle:
    def test_receipt_numbers_are_sequential_per_store(self, kiosk, product):
        o1 = services.set_awaiting_payment(_order_with_item(kiosk, product))
        o2 = services.set_awaiting_payment(_order_with_item(kiosk, product))
        assert services.finalize(o1).receipt_number == 1
        assert services.finalize(o2).receipt_number == 2
        kiosk.store.refresh_from_db()
        assert kiosk.store.next_receipt_number == 3

    def test_finalize_is_idempotent(self, kiosk, product):
        o = services.finalize(services.set_awaiting_payment(_order_with_item(kiosk, product)))
        assert services.finalize(o).receipt_number == o.receipt_number

    def test_empty_order_cannot_await_payment(self, kiosk):
        from apps.core.exceptions import EmptyOrder

        with pytest.raises(EmptyOrder):
            services.set_awaiting_payment(Order.objects.create(store=kiosk.store, kiosk=kiosk))

    def test_reopen_after_failed_payment(self, kiosk, product):
        o = services.set_awaiting_payment(_order_with_item(kiosk, product))
        assert services.reopen(o).status == Order.Status.OPEN


def test_cors_preflight_allows_idempotency_key(client, settings):
    """The Flutter web kiosk sends Idempotency-Key on POST items/ and payments/; the preflight must allow it."""
    settings.CORS_ALLOWED_ORIGINS = ["http://kiosk.test"]
    resp = client.options(
        "/api/v1/kiosk/orders/",
        HTTP_ORIGIN="http://kiosk.test",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="authorization,content-type,idempotency-key",
    )
    assert resp.status_code == 200
    assert "idempotency-key" in resp["Access-Control-Allow-Headers"]
