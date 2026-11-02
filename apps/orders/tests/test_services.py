"""Business rules of the basket: weight check, idempotency, override, remove, cancel, totals."""

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.core.exceptions import APIError, Conflict, NotFound, OrderNotOpen, ProductNotFound, WeightMismatch
from apps.orders import services
from apps.orders.models import KioskEvent, Order, OrderItem, WeightVerification
from apps.orders.tasks import cancel_stale_orders

pytestmark = pytest.mark.django_db


def _fail(order, kiosk, product, measured):
    with pytest.raises(WeightMismatch) as exc:
        services.add_item(order, kiosk, product.barcode, measured)
    return exc.value


class TestWeightCheck:
    def test_pass_within_tolerance_creates_item_and_verification(self, order, kiosk, product):
        item = services.add_item(order, kiosk, product.barcode, 508)  # store default tolerance 10
        assert item.weight_status == OrderItem.WeightStatus.PASSED
        assert item.tolerance_g == 10 and item.measured_weight_g == 508
        v = item.verification
        assert (v.result, v.outcome, v.delta_g) == ("PASS", "ADDED", 8)
        assert item.unit_price == product.price and item.product_name == product.name

    def test_boundary_is_inclusive(self, order, kiosk, product):
        assert services.add_item(order, kiosk, product.barcode, 490).weight_status == "PASSED"

    def test_fail_records_rejected_verification_and_no_item(self, order, kiosk, product):
        err = _fail(order, kiosk, product, 540)
        v = WeightVerification.objects.get()
        assert (v.result, v.outcome) == ("FAIL", "REJECTED")
        assert err.details == {"verification_id": v.id, "expected_weight_g": 500, "measured_weight_g": 540,
                               "tolerance_g": 10, "delta_g": 40}
        assert not order.items.exists()

    def test_product_tolerance_overrides_store_default(self, order, kiosk, make_product):
        p = make_product(barcode="B2", tolerance=50)
        item = services.add_item(order, kiosk, "B2", 540)
        assert item.tolerance_g == 50
        _fail(order, kiosk, p, 551)

    def test_skip_when_weight_check_not_required(self, order, kiosk, make_product):
        make_product(barcode="GUM", requires_weight_check=False, price="0.50")
        item = services.add_item(order, kiosk, "GUM", None)
        assert item.weight_status == OrderItem.WeightStatus.SKIPPED
        assert item.verification is None and item.measured_weight_g is None
        assert not WeightVerification.objects.exists()

    def test_unknown_inactive_or_other_store_barcode(self, order, kiosk, make_product, other_store):
        make_product(barcode="OFF", is_active=False)
        make_product(barcode="ELSEWHERE", store_=other_store)
        for code in ("NOPE", "OFF", "ELSEWHERE"):
            with pytest.raises(ProductNotFound):
                services.add_item(order, kiosk, code, 500)

    def test_add_requires_open_order(self, order, kiosk, product):
        services.add_item(order, kiosk, product.barcode, 500)
        services.set_awaiting_payment(order)
        with pytest.raises(OrderNotOpen):
            services.add_item(order, kiosk, product.barcode, 500)

    def test_idempotent_replay_returns_same_item(self, order, kiosk, product):
        key = uuid.uuid4()
        first = services.add_item(order, kiosk, product.barcode, 500, client_request_id=key)
        again = services.add_item(order, kiosk, product.barcode, 500, client_request_id=key)
        assert again.pk == first.pk and again.replayed and not first.replayed
        assert order.items.count() == 1 and WeightVerification.objects.count() == 1


class TestTotals:
    def test_tax_rounds_half_up(self, order, kiosk, store, make_product):
        store.tax_rate = Decimal("0.05")
        store.save()
        make_product(barcode="A", price="0.10", requires_weight_check=False)
        services.add_item(order, kiosk, "A", None)  # 0.10 * 0.05 = 0.005 -> 0.01
        order.refresh_from_db()
        assert (order.subtotal, order.tax, order.total) == (Decimal("0.10"), Decimal("0.01"), Decimal("0.11"))

    def test_totals_sum_items(self, order, kiosk, store, product):
        store.tax_rate = Decimal("0.10")
        store.save()
        services.add_item(order, kiosk, product.barcode, 500)
        services.add_item(order, kiosk, product.barcode, 500)
        assert (order.subtotal, order.tax, order.total) == (Decimal("5.00"), Decimal("0.50"), Decimal("5.50"))


class TestOverride:
    def test_happy_path(self, order, kiosk, product, staff_user):
        vid = _fail(order, kiosk, product, 540).details["verification_id"]
        item = services.override_item(order, vid, staff_user, "bag is heavier")
        assert item.weight_status == OrderItem.WeightStatus.OVERRIDDEN
        assert item.measured_weight_g == 540 and item.overridden_by == staff_user
        v = WeightVerification.objects.get(pk=vid)
        assert (v.outcome, v.override_by, v.override_reason) == ("OVERRIDDEN", staff_user, "bag is heavier")
        assert order.total == product.price

    def test_already_overridden(self, order, kiosk, product, staff_user):
        vid = _fail(order, kiosk, product, 540).details["verification_id"]
        services.override_item(order, vid, staff_user, "ok")
        with pytest.raises(Conflict) as exc:
            services.override_item(order, vid, staff_user, "again")
        assert exc.value.code == "VERIFICATION_NOT_OVERRIDABLE"

    def test_passed_verification_not_overridable(self, order, kiosk, product, staff_user):
        item = services.add_item(order, kiosk, product.barcode, 500)
        with pytest.raises(Conflict):
            services.override_item(order, item.verification_id, staff_user, "")

    def test_verification_of_other_order(self, order, kiosk, product, staff_user):
        other = services.open_order(kiosk)
        vid = _fail(other, kiosk, product, 540).details["verification_id"]
        with pytest.raises(NotFound) as exc:
            services.override_item(order, vid, staff_user, "")
        assert exc.value.code == "VERIFICATION_NOT_FOUND"


class TestRemoveAndCancel:
    def test_remove_item_recomputes(self, order, kiosk, product, staff_user):
        item = services.add_item(order, kiosk, product.barcode, 500)
        services.remove_item(order, item.pk, staff_user)
        assert not order.items.exists() and order.total == 0

    def test_remove_unknown_item(self, order, staff_user):
        with pytest.raises(NotFound):
            services.remove_item(order, 999, staff_user)

    def test_cancel_empty_without_staff(self, order):
        services.cancel_order(order, reason="changed mind")
        order.refresh_from_db()
        assert order.status == Order.Status.CANCELLED and order.cancelled_at
        assert order.cancel_reason == "changed mind"

    def test_cancel_with_items_needs_staff(self, order, kiosk, product, staff_user):
        services.add_item(order, kiosk, product.barcode, 500)
        with pytest.raises(APIError) as exc:
            services.cancel_order(order)
        assert exc.value.code == "STAFF_REQUIRED"
        assert services.cancel_order(order, staff_user=staff_user).status == Order.Status.CANCELLED

    def test_idle_timeout_cancel_with_items_needs_no_staff(self, order, kiosk, product):
        services.add_item(order, kiosk, product.barcode, 500)
        services.cancel_order(order, reason=services.IDLE_TIMEOUT_REASON)
        order.refresh_from_db()
        assert order.status == Order.Status.CANCELLED and order.cancel_reason == "IDLE_TIMEOUT"

    def test_other_reason_with_items_still_needs_staff(self, order, kiosk, product):
        services.add_item(order, kiosk, product.barcode, 500)
        with pytest.raises(APIError) as exc:
            services.cancel_order(order, reason="idle_timeout")
        assert exc.value.code == "STAFF_REQUIRED" and exc.value.status_code == 403

    def test_cannot_cancel_awaiting_payment(self, order, kiosk, product, staff_user):
        services.add_item(order, kiosk, product.barcode, 500)
        services.set_awaiting_payment(order)
        with pytest.raises(OrderNotOpen):
            services.cancel_order(order, staff_user=staff_user)


class TestStaleTask:
    def test_cancels_only_idle_open_orders(self, kiosk, product, settings):
        settings.STALE_ORDER_MINUTES = 30
        old = timezone.now() - timedelta(minutes=31)
        stale = services.open_order(kiosk)
        fresh = services.open_order(kiosk)
        waiting = services.open_order(kiosk)
        services.add_item(waiting, kiosk, product.barcode, 500)
        services.set_awaiting_payment(waiting)
        Order.objects.filter(pk__in=[stale.pk, waiting.pk]).update(updated_at=old)

        assert cancel_stale_orders() == 1
        stale.refresh_from_db()
        assert (stale.status, stale.cancel_reason) == ("CANCELLED", "STALE")
        assert Order.objects.get(pk=fresh.pk).status == "OPEN"
        assert Order.objects.get(pk=waiting.pk).status == "AWAITING_PAYMENT"


class TestKioskEvents:
    def test_record_with_and_without_order(self, order, kiosk):
        ev = services.record_kiosk_event(kiosk, KioskEvent.Type.STAFF_CALLED, order=order, detail={"why": "help"})
        assert (ev.store_id, ev.kiosk_id, ev.order_id) == (kiosk.store_id, kiosk.id, order.id)
        assert ev.detail == {"why": "help"}
        ev = services.record_kiosk_event(kiosk, KioskEvent.Type.APP_STARTED)
        assert ev.order is None and ev.detail == {} and ev.store_id == kiosk.store_id

    def test_other_store_order_is_not_found(self, kiosk, other_store, make_kiosk):
        other_kiosk = make_kiosk(other_store, "Other", "SN-O2")
        foreign = services.open_order(other_kiosk)
        with pytest.raises(NotFound):
            services.record_kiosk_event(kiosk, KioskEvent.Type.STAFF_CALLED, order=foreign)
        assert not KioskEvent.objects.exists()
