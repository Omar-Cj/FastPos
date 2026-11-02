"""Order lifecycle services.

Rules live here, not in views, so the kiosk API, the simulator and tests share one implementation.

Foundation (already implemented, keep the signatures — the payments app calls them):
    set_awaiting_payment(order)  OPEN -> AWAITING_PAYMENT
    reopen(order)                AWAITING_PAYMENT -> OPEN   (payment failed, customer retries)
    finalize(order)              AWAITING_PAYMENT -> PAID   (assigns per-store receipt number)

Basket services:
    open_order(kiosk) -> Order
    add_item(order, kiosk, barcode, measured_weight_g, client_request_id=None) -> OrderItem
        raises ProductNotFound / OrderNotOpen / WeightMismatch(details={"verification_id": ...})
    override_item(order, verification_id, staff_user, reason) -> OrderItem
    remove_item(order, item_id, staff_user) -> None
    cancel_order(order, staff_user=None, reason="") -> Order   (no staff needed for IDLE_TIMEOUT)
    record_kiosk_event(kiosk, event_type, order=None, detail=None) -> KioskEvent
    recompute_totals(order) -> Order
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.catalog.models import Product
from apps.core.exceptions import (
    APIError,
    Conflict,
    EmptyOrder,
    NotFound,
    OrderNotOpen,
    ProductNotFound,
    WeightMismatch,
)
from apps.stores.models import Store

from .models import KioskEvent, Order, OrderItem, WeightVerification

CENT = Decimal("0.01")
# The kiosk abandons an unattended basket on its own; a kiosk is an authenticated device, so no staff needed.
IDLE_TIMEOUT_REASON = "IDLE_TIMEOUT"


def set_awaiting_payment(order: Order) -> Order:
    """Lock the basket while a payment attempt runs. Requires at least one item."""
    if order.status != Order.Status.OPEN:
        raise OrderNotOpen()
    if not order.items.exists():
        raise EmptyOrder()
    order.status = Order.Status.AWAITING_PAYMENT
    order.save(update_fields=["status", "updated_at"])
    return order


def reopen(order: Order) -> Order:
    """A payment attempt failed: give the basket back to the customer for a retry."""
    if order.status != Order.Status.AWAITING_PAYMENT:
        raise Conflict("Order is not awaiting payment.", code="ORDER_NOT_AWAITING_PAYMENT")
    order.status = Order.Status.OPEN
    order.save(update_fields=["status", "updated_at"])
    return order


@transaction.atomic
def finalize(order: Order) -> Order:
    """Mark the order PAID and assign the next gapless receipt number for its store.

    The Store row is locked so two kiosks finishing at the same moment never share a number.
    Idempotent: a PAID order is returned unchanged.
    """
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status == Order.Status.PAID:
        return order
    if order.status != Order.Status.AWAITING_PAYMENT:
        raise Conflict("Order is not awaiting payment.", code="ORDER_NOT_AWAITING_PAYMENT")

    store = Store.objects.select_for_update().get(pk=order.store_id)
    order.receipt_number = store.next_receipt_number
    store.next_receipt_number += 1
    store.save(update_fields=["next_receipt_number", "updated_at"])

    order.status = Order.Status.PAID
    order.paid_at = timezone.now()
    order.save(update_fields=["status", "receipt_number", "paid_at", "updated_at"])
    return order


# --- basket -------------------------------------------------------------------------------------

def open_order(kiosk) -> Order:
    """Start a new empty basket for this kiosk."""
    return Order.objects.create(store=kiosk.store, kiosk=kiosk)


def recompute_totals(order: Order) -> Order:
    """subtotal = sum of unit prices; tax = subtotal * store.tax_rate rounded half-up to cents."""
    subtotal = order.items.aggregate(s=Sum("unit_price"))["s"] or Decimal("0")
    subtotal = Decimal(subtotal).quantize(CENT, rounding=ROUND_HALF_UP)
    tax = (subtotal * order.store.tax_rate).quantize(CENT, rounding=ROUND_HALF_UP)
    order.subtotal = subtotal
    order.tax = tax
    order.total = subtotal + tax
    order.save(update_fields=["subtotal", "tax", "total", "updated_at"])
    return order


def _lock_open_order(order: Order) -> Order:
    """Re-read the order under a row lock (serialises concurrent scans) and require OPEN."""
    locked = Order.objects.select_for_update().select_related("store").get(pk=order.pk)
    if locked.status != Order.Status.OPEN:
        raise OrderNotOpen()
    return locked


def _sync(target: Order, source: Order) -> None:
    """Copy fresh state back onto the caller's instance so it doesn't hold stale totals."""
    for f in ("status", "subtotal", "tax", "total", "updated_at", "cancelled_at", "cancel_reason"):
        setattr(target, f, getattr(source, f))


def _item_from_product(order, product, **fields) -> OrderItem:
    return OrderItem.objects.create(
        order=order,
        product=product,
        barcode=product.barcode,
        product_name=product.name,
        unit_price=product.price,
        expected_weight_g=product.expected_weight_g,
        **fields,
    )


def add_item(order: Order, kiosk, barcode: str, measured_weight_g: int | None,
             client_request_id=None) -> OrderItem:
    """Scan + weigh one unit.

    Returns the created OrderItem. On an idempotent replay the existing item is returned with
    `item.replayed = True`. A failed weigh records a REJECTED WeightVerification (committed) and
    then raises WeightMismatch with the numbers the kiosk needs to show / request an override.
    """
    with transaction.atomic():
        locked = _lock_open_order(order)

        if client_request_id is not None:
            existing = OrderItem.objects.filter(order=locked, client_request_id=client_request_id).first()
            if existing is not None:
                existing.replayed = True
                _sync(order, locked)
                return existing

        product = (
            Product.objects.select_related("store")
            .filter(store_id=locked.store_id, barcode=barcode, is_active=True)
            .first()
        )
        if product is None:
            raise ProductNotFound()

        tolerance = product.effective_tolerance_g()

        if not product.requires_weight_check:
            item = _item_from_product(
                locked, product, tolerance_g=tolerance, measured_weight_g=measured_weight_g,
                weight_status=OrderItem.WeightStatus.SKIPPED, client_request_id=client_request_id,
            )
            recompute_totals(locked)
            _sync(order, locked)
            item.replayed = False
            return item

        if measured_weight_g is None:
            raise ValidationError({"measured_weight_g": ["Required for this product."]})

        delta = measured_weight_g - product.expected_weight_g
        passed = abs(delta) <= tolerance
        verification = WeightVerification.objects.create(
            order=locked,
            kiosk=kiosk,
            product=product,
            expected_weight_g=product.expected_weight_g,
            tolerance_g=tolerance,
            measured_weight_g=measured_weight_g,
            delta_g=delta,
            result=WeightVerification.Result.PASS if passed else WeightVerification.Result.FAIL,
            outcome=WeightVerification.Outcome.ADDED if passed else WeightVerification.Outcome.REJECTED,
        )
        if passed:
            item = _item_from_product(
                locked, product, tolerance_g=tolerance, measured_weight_g=measured_weight_g,
                weight_status=OrderItem.WeightStatus.PASSED, verification=verification,
                client_request_id=client_request_id,
            )
            recompute_totals(locked)
            _sync(order, locked)
            item.replayed = False
            return item

    # Raised outside the atomic block so the REJECTED verification row is kept for audit.
    raise WeightMismatch(details={
        "verification_id": verification.id,
        "expected_weight_g": verification.expected_weight_g,
        "measured_weight_g": verification.measured_weight_g,
        "tolerance_g": verification.tolerance_g,
        "delta_g": verification.delta_g,
    })


@transaction.atomic
def override_item(order: Order, verification_id: int, staff_user, reason: str) -> OrderItem:
    """Staff approve a rejected weigh: the unit is added with weight_status OVERRIDDEN."""
    locked = _lock_open_order(order)
    verification = (
        WeightVerification.objects.select_for_update()
        .filter(pk=verification_id, order=locked)
        .first()
    )
    if verification is None:
        raise NotFound("Weight verification not found for this order.", code="VERIFICATION_NOT_FOUND")
    if verification.outcome != WeightVerification.Outcome.REJECTED:
        raise Conflict("Only a rejected weigh can be overridden.", code="VERIFICATION_NOT_OVERRIDABLE")

    product = verification.product
    item = _item_from_product(
        locked, product,
        tolerance_g=verification.tolerance_g,
        measured_weight_g=verification.measured_weight_g,
        weight_status=OrderItem.WeightStatus.OVERRIDDEN,
        verification=verification,
        overridden_by=staff_user,
    )
    verification.outcome = WeightVerification.Outcome.OVERRIDDEN
    verification.override_by = staff_user
    verification.override_reason = reason or ""
    verification.save(update_fields=["outcome", "override_by", "override_reason"])
    recompute_totals(locked)
    _sync(order, locked)
    return item


@transaction.atomic
def remove_item(order: Order, item_id: int, staff_user) -> None:
    """Staff remove one unit from an OPEN basket. `staff_user` is verified by the caller."""
    locked = _lock_open_order(order)
    deleted, _ = OrderItem.objects.filter(pk=item_id, order=locked).delete()
    if not deleted:
        raise NotFound("Item not found in this order.", code="ITEM_NOT_FOUND")
    recompute_totals(locked)
    _sync(order, locked)


@transaction.atomic
def cancel_order(order: Order, staff_user=None, reason: str = "") -> Order:
    """Abandon an OPEN basket. Empty baskets need no staff; baskets with items do,
    unless the kiosk itself is cancelling an unattended basket (`reason == IDLE_TIMEOUT_REASON`)."""
    locked = _lock_open_order(order)
    if staff_user is None and reason != IDLE_TIMEOUT_REASON and locked.items.exists():
        raise APIError("Staff approval is required to cancel an order with items.",
                       code="STAFF_REQUIRED", status_code=403)
    locked.status = Order.Status.CANCELLED
    locked.cancelled_at = timezone.now()
    locked.cancel_reason = (reason or "")[:255]
    locked.save(update_fields=["status", "cancelled_at", "cancel_reason", "updated_at"])
    _sync(order, locked)
    return locked


# --- kiosk events -------------------------------------------------------------------------------

def record_kiosk_event(kiosk, event_type: str, order: Order | None = None, detail: dict | None = None) -> KioskEvent:
    """Log an operational event from a kiosk. `order`, when given, must belong to the kiosk's store."""
    if order is not None and order.store_id != kiosk.store_id:
        raise NotFound("Order not found.", code="ORDER_NOT_FOUND")
    return KioskEvent.objects.create(
        store_id=kiosk.store_id, kiosk=kiosk, order=order, type=event_type, detail=detail or {},
    )
