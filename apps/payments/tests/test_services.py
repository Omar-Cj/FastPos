import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.exceptions import Conflict, EmptyOrder, OrderNotOpen, OrderNotPaid, PaymentInProgress
from apps.orders.models import Order
from apps.payments import services
from apps.payments.models import Payment
from apps.payments.providers.base import ProviderResult
from apps.payments.tasks import process_payment, reconcile_pending_payments

from .helpers import order_with_items


@pytest.fixture
def order(kiosk, product):
    return order_with_items(kiosk, product)


def initiate(capture, order, method="WAAFI", phone="252615000001", key=None):
    with capture(execute=True):
        payment, created = services.initiate_payment(order, method, phone, client_request_id=key)
    payment.refresh_from_db()
    order.refresh_from_db()
    return payment, created


class TestInitiate:
    def test_success_assigns_receipt_number(self, order, django_capture_on_commit_callbacks):
        payment, created = initiate(django_capture_on_commit_callbacks, order)
        assert created
        assert payment.status == Payment.Status.SUCCESS
        assert payment.amount == order.total and payment.currency == "USD"
        assert payment.provider_reference.startswith("MOCK-") and payment.completed_at
        assert order.status == Order.Status.PAID and order.receipt_number == 1

    def test_pending_until_commit(self, order, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            payment, _ = services.initiate_payment(order, "WAAFI", "252615000001")
        assert len(callbacks) == 1
        order.refresh_from_db()
        assert payment.status == Payment.Status.PENDING
        assert order.status == Order.Status.AWAITING_PAYMENT

    def test_fail_reopens_then_retry_with_other_method(self, order, django_capture_on_commit_callbacks):
        failed, _ = initiate(django_capture_on_commit_callbacks, order, phone="252610000000")
        assert failed.status == Payment.Status.FAILED and failed.failure_reason
        assert order.status == Order.Status.OPEN and order.receipt_number is None

        ok, _ = initiate(django_capture_on_commit_callbacks, order, method="EDAHAB", phone="252655000001")
        assert ok.status == Payment.Status.SUCCESS
        assert order.status == Order.Status.PAID
        assert order.payments.count() == 2

    def test_timeout_fails(self, order, django_capture_on_commit_callbacks):
        payment, _ = initiate(django_capture_on_commit_callbacks, order, phone="252619999")
        assert (payment.status, payment.failure_reason) == (Payment.Status.FAILED, "TIMEOUT")

    def test_payment_in_progress(self, order, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=False):
            services.initiate_payment(order, "WAAFI", "252615000001")
        with pytest.raises(PaymentInProgress):
            services.initiate_payment(order, "EDAHAB", "252615000001")

    def test_idempotent_replay(self, order, django_capture_on_commit_callbacks):
        key = uuid.uuid4()
        first, created1 = initiate(django_capture_on_commit_callbacks, order, key=key)
        again, created2 = initiate(django_capture_on_commit_callbacks, order, key=key)
        assert (created1, created2) == (True, False)
        assert first.pk == again.pk and Payment.objects.count() == 1

    def test_empty_order(self, kiosk, django_capture_on_commit_callbacks):
        order = Order.objects.create(store=kiosk.store, kiosk=kiosk)
        with pytest.raises(EmptyOrder):
            initiate(django_capture_on_commit_callbacks, order)
        assert not Payment.objects.exists()

    def test_paid_order_not_open(self, order, django_capture_on_commit_callbacks):
        initiate(django_capture_on_commit_callbacks, order)
        with pytest.raises(OrderNotOpen):
            initiate(django_capture_on_commit_callbacks, order)


class TestCompleteAndTasks:
    def _pending(self, order, django_capture_on_commit_callbacks, phone="252615000001"):
        with django_capture_on_commit_callbacks(execute=False):
            payment, _ = services.initiate_payment(order, "WAAFI", phone)
        return payment

    def test_complete_is_idempotent(self, order, django_capture_on_commit_callbacks):
        payment = self._pending(order, django_capture_on_commit_callbacks)
        services.complete_payment(payment, ProviderResult("SUCCESS", reference="R1"))
        again = services.complete_payment(payment, ProviderResult("FAILED", message="late"))
        assert again.status == Payment.Status.SUCCESS and again.provider_reference == "R1"

    def test_pending_result_keeps_pending(self, order, django_capture_on_commit_callbacks):
        payment = self._pending(order, django_capture_on_commit_callbacks)
        p = services.complete_payment(payment, ProviderResult("PENDING", reference="INV1"))
        assert p.status == Payment.Status.PENDING and p.provider_reference == "INV1"

    def test_provider_crash_fails_payment(self, order, django_capture_on_commit_callbacks, monkeypatch):
        payment = self._pending(order, django_capture_on_commit_callbacks)

        class Boom:
            def charge(self, payment):
                raise RuntimeError("kaboom")

        monkeypatch.setattr("apps.payments.tasks.get_provider", lambda method: Boom())
        assert process_payment(str(payment.id)) == "FAILED"
        payment.refresh_from_db()
        order.refresh_from_db()
        assert payment.failure_reason.startswith("PROVIDER_ERROR: kaboom")
        assert order.status == Order.Status.OPEN

    def test_process_payment_skips_terminal(self, order, django_capture_on_commit_callbacks):
        payment, _ = initiate(django_capture_on_commit_callbacks, order)
        assert process_payment(str(payment.id)) is None

    def test_reconcile_picks_up_old_pending(self, kiosk, product, django_capture_on_commit_callbacks):
        old = self._pending(order_with_items(kiosk, product), django_capture_on_commit_callbacks)
        old_fail = self._pending(order_with_items(kiosk, product), django_capture_on_commit_callbacks,
                                 phone="252610000000")
        fresh = self._pending(order_with_items(kiosk, product), django_capture_on_commit_callbacks)
        Payment.objects.filter(pk__in=[old.pk, old_fail.pk]).update(
            initiated_at=timezone.now() - timedelta(minutes=10))

        assert reconcile_pending_payments() == 2
        old.refresh_from_db()
        old_fail.refresh_from_db()
        fresh.refresh_from_db()
        assert old.status == Payment.Status.SUCCESS and old.order.status == Order.Status.PAID
        assert old_fail.status == Payment.Status.FAILED and old_fail.order.status == Order.Status.OPEN
        assert fresh.status == Payment.Status.PENDING

    def test_reconcile_gives_up_eventually(self, order, django_capture_on_commit_callbacks, monkeypatch):
        payment = self._pending(order, django_capture_on_commit_callbacks)
        Payment.objects.filter(pk=payment.pk).update(initiated_at=timezone.now() - timedelta(hours=2))

        class StillPending:
            def check_status(self, payment):
                return ProviderResult("PENDING")

        monkeypatch.setattr("apps.payments.tasks.get_provider", lambda method: StillPending())
        reconcile_pending_payments()
        payment.refresh_from_db()
        assert (payment.status, payment.failure_reason) == (Payment.Status.FAILED, "RECONCILE_TIMEOUT")


class TestAbandon:
    def test_pending_mock_phone_stays_pending(self, order, django_capture_on_commit_callbacks):
        payment, _ = initiate(django_capture_on_commit_callbacks, order, phone="252618888")
        assert payment.status == Payment.Status.PENDING
        assert payment.provider_response == {"outcome": "pending"}
        assert order.status == Order.Status.AWAITING_PAYMENT

    def test_pending_to_failed_reopens_order(self, order, django_capture_on_commit_callbacks):
        payment, _ = initiate(django_capture_on_commit_callbacks, order, phone="252618888")
        result = services.abandon_payment(payment)
        assert result.status == Payment.Status.FAILED
        assert result.failure_reason == services.ABANDON_REASON == "Abandoned by customer"
        assert result.completed_at
        assert result.order.status == Order.Status.OPEN
        order.refresh_from_db()
        assert order.status == Order.Status.OPEN

    def test_failed_is_unchanged(self, order, django_capture_on_commit_callbacks):
        payment, _ = initiate(django_capture_on_commit_callbacks, order, phone="252610000000")
        result = services.abandon_payment(payment)
        assert (result.status, result.failure_reason) == (Payment.Status.FAILED, "Customer declined")
        assert result.completed_at == payment.completed_at

    def test_success_conflict(self, order, django_capture_on_commit_callbacks):
        payment, _ = initiate(django_capture_on_commit_callbacks, order)
        with pytest.raises(Conflict) as exc:
            services.abandon_payment(payment)
        assert exc.value.code == "PAYMENT_NOT_PENDING" and exc.value.status_code == 409
        payment.refresh_from_db()
        assert payment.status == Payment.Status.SUCCESS


class TestReceipt:
    def test_groups_identical_products(self, kiosk, make_product, django_capture_on_commit_callbacks):
        rice = make_product()
        milk = make_product(barcode="111", name="Milk 1L", price="1.20", expected_weight_g=1050)
        order = order_with_items(kiosk, rice, milk, rice)
        payment, _ = initiate(django_capture_on_commit_callbacks, order)
        receipt = services.build_receipt(order)
        assert receipt["receipt_number"] == 1 and receipt["item_count"] == 3
        assert [(line["name"], line["quantity"], line["line_total"]) for line in receipt["lines"]] == [
            ("Rice 500g", 2, "5.00"), ("Milk 1L", 1, "1.20")]
        assert receipt["total"] == "6.20"
        assert receipt["payment"]["provider_reference"] == payment.provider_reference
        assert receipt["store"]["name"] == "Demo Mart" and receipt["kiosk"] == "Kiosk 1"

    def test_unpaid_order(self, order):
        with pytest.raises(OrderNotPaid):
            services.build_receipt(order)
