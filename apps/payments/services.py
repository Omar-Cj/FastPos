"""Payment lifecycle.

    initiate_payment  -> Payment(PENDING), order AWAITING_PAYMENT, process_payment queued on commit
    process_payment   -> provider.charge() -> complete_payment()
    complete_payment  -> SUCCESS: order PAID (receipt number)   FAILED: order OPEN again (retry)
    reconcile task    -> settles PENDING payments via provider.check_status()
    abandon_payment   -> kiosk gives up on a PENDING payment: FAILED "Abandoned by customer", order OPEN
"""

from __future__ import annotations

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.exceptions import Conflict, OrderNotOpen, OrderNotPaid, PaymentInProgress
from apps.orders import services as order_services
from apps.orders.models import Order

from .models import Payment
from .providers.base import ProviderResult

ABANDON_REASON = "Abandoned by customer"


def initiate_payment(order: Order, method: str, customer_phone: str,
                     client_request_id=None) -> tuple[Payment, bool]:
    """Start a payment attempt. Returns (payment, created); a replayed Idempotency-Key returns the
    original attempt with created=False."""
    if client_request_id:
        existing = Payment.objects.filter(order=order, client_request_id=client_request_id).first()
        if existing:
            return existing, False

    try:
        with transaction.atomic():
            order = Order.objects.select_for_update().select_related("store").get(pk=order.pk)
            if order.payments.filter(status=Payment.Status.PENDING).exists():
                raise PaymentInProgress()
            if order.status != Order.Status.OPEN:
                raise OrderNotOpen()
            recompute = getattr(order_services, "recompute_totals", None)
            if recompute is not None and order.items.exists():
                order = recompute(order) or order
            payment = Payment.objects.create(
                order=order, method=method, customer_phone=customer_phone,
                amount=order.total, currency=order.store.currency,
                status=Payment.Status.PENDING, client_request_id=client_request_id,
            )
            order_services.set_awaiting_payment(order)
            payment_id = str(payment.id)
            transaction.on_commit(lambda: _enqueue(payment_id))
    except IntegrityError:
        # Two retries with the same key raced; the other one won.
        if client_request_id:
            existing = Payment.objects.filter(order=order, client_request_id=client_request_id).first()
            if existing:
                return existing, False
        raise
    return payment, True


def _enqueue(payment_id: str) -> None:
    from .tasks import process_payment

    process_payment.delay(payment_id)


def complete_payment(payment: Payment, result: ProviderResult) -> Payment:
    """Apply a provider outcome. Idempotent: a payment that is already SUCCESS/FAILED is untouched."""
    with transaction.atomic():
        payment = Payment.objects.select_for_update().select_related("order").get(pk=payment.pk)
        if payment.status != Payment.Status.PENDING:
            return payment

        fields = ["provider_request", "provider_response", "provider_reference"]
        if result.request is not None:
            payment.provider_request = result.request
        if result.response is not None:
            payment.provider_response = result.response
        if result.reference:
            payment.provider_reference = result.reference[:128]

        if result.status == "SUCCESS":
            payment.status = Payment.Status.SUCCESS
            payment.completed_at = timezone.now()
            fields += ["status", "completed_at"]
            payment.save(update_fields=fields)
            order_services.finalize(payment.order)
        elif result.status == "FAILED":
            payment.status = Payment.Status.FAILED
            payment.failure_reason = (result.message or "Payment failed")[:255]
            payment.completed_at = timezone.now()
            fields += ["status", "failure_reason", "completed_at"]
            payment.save(update_fields=fields)
            order = payment.order
            if order.status == Order.Status.AWAITING_PAYMENT:
                order_services.reopen(order)
        else:  # PENDING: keep waiting, the reconcile task settles it.
            payment.save(update_fields=fields)
    return payment


def abandon_payment(payment: Payment) -> Payment:
    """Kiosk-initiated cancel of a payment attempt. Idempotent for FAILED; a SUCCESS cannot be undone."""
    with transaction.atomic():
        payment = Payment.objects.select_for_update().select_related("order").get(pk=payment.pk)
        if payment.status == Payment.Status.SUCCESS:
            raise Conflict("Payment already succeeded.", code="PAYMENT_NOT_PENDING")
        if payment.status == Payment.Status.PENDING:
            # Same transaction keeps the row lock; complete_payment's nested atomic is a savepoint.
            complete_payment(payment, ProviderResult("FAILED", message=ABANDON_REASON))
    # Fresh fetch (not refresh_from_db) so the cached order reflects the reopen.
    return Payment.objects.select_related("order").get(pk=payment.pk)


def build_receipt(order: Order) -> dict:
    if order.status != Order.Status.PAID:
        raise OrderNotPaid()

    # One line per (product, unit price), in the order products were first scanned.
    lines: dict = {}
    item_count = 0
    for item in order.items.all().order_by("created_at", "id"):
        item_count += 1
        key = (item.product_id, item.unit_price)
        line = lines.get(key)
        if line is None:
            line = lines[key] = {"barcode": item.barcode, "name": item.product_name, "quantity": 0,
                                 "unit_price": item.unit_price, "line_total": Decimal("0")}
        line["quantity"] += 1
        line["line_total"] += item.unit_price

    payment = order.payments.filter(status=Payment.Status.SUCCESS).first()
    return {
        "receipt_number": order.receipt_number,
        "order_id": str(order.id),
        "store": {"name": order.store.name, "address": order.store.address},
        "kiosk": order.kiosk.name,
        "paid_at": order.paid_at.isoformat() if order.paid_at else None,
        "currency": order.store.currency,
        "lines": [
            {**line, "unit_price": _money(line["unit_price"]), "line_total": _money(line["line_total"])}
            for line in lines.values()
        ],
        "subtotal": _money(order.subtotal),
        "tax": _money(order.tax),
        "total": _money(order.total),
        "payment": {
            "method": payment.method,
            "provider_reference": payment.provider_reference,
            "customer_phone": payment.customer_phone,
            "completed_at": payment.completed_at.isoformat() if payment.completed_at else None,
        } if payment else None,
        "item_count": item_count,
    }


def _money(value) -> str:
    return str(Decimal(value).quantize(Decimal("0.01")))


def record_event(order: Order, event_type: str):
    """Kiosk hardware events after payment. Idempotent: the first timestamp is kept."""
    field = {"RECEIPT_PRINTED": "receipt_printed_at", "EAS_DEACTIVATED": "eas_deactivated_at"}[event_type]
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order.pk)
        if order.status != Order.Status.PAID:
            raise OrderNotPaid()
        if getattr(order, field) is None:
            setattr(order, field, timezone.now())
            order.save(update_fields=[field, "updated_at"])
    return getattr(order, field)
