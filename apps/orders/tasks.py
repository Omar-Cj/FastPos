"""Periodic tasks for orders."""

from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .models import Order


@shared_task
def cancel_stale_orders() -> int:
    """Cancel OPEN orders idle longer than settings.STALE_ORDER_MINUTES. Returns count.

    System action: no staff needed. AWAITING_PAYMENT orders are left to the payments flow.
    """
    now = timezone.now()
    cutoff = now - timedelta(minutes=settings.STALE_ORDER_MINUTES)
    return Order.objects.filter(status=Order.Status.OPEN, updated_at__lt=cutoff).update(
        status=Order.Status.CANCELLED, cancelled_at=now, cancel_reason="STALE", updated_at=now
    )
