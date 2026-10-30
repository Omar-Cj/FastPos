"""Celery tasks for the payment lifecycle.

process_payment             runs one provider charge (blocks up to PAYMENT_HTTP_TIMEOUT_SECONDS)
reconcile_pending_payments  beat, every minute: settles PENDING payments stuck past the threshold
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .models import Payment
from .providers.base import ProviderResult
from .providers.registry import get_provider
from .services import complete_payment

logger = logging.getLogger(__name__)

# PENDING payments still unresolved after this long are failed so the basket is released.
GIVE_UP_MINUTES_DEFAULT = 30


@shared_task(soft_time_limit=140)
def process_payment(payment_id: str) -> str | None:
    payment = Payment.objects.select_related("order").filter(pk=payment_id).first()
    if payment is None or payment.status != Payment.Status.PENDING:
        return None
    try:
        result = get_provider(payment.method).charge(payment)
    except Exception as exc:  # noqa: BLE001 - any provider crash must release the order
        logger.exception("payment %s provider error", payment_id)
        result = ProviderResult("FAILED", message=f"PROVIDER_ERROR: {exc}"[:255])
    return complete_payment(payment, result).status


@shared_task
def reconcile_pending_payments() -> int:
    now = timezone.now()
    cutoff = now - timedelta(minutes=settings.PAYMENT_RECONCILE_AFTER_MINUTES)
    give_up = now - timedelta(minutes=getattr(settings, "PAYMENT_RECONCILE_GIVE_UP_MINUTES",
                                              GIVE_UP_MINUTES_DEFAULT))
    processed = 0
    pending = Payment.objects.select_related("order").filter(
        status=Payment.Status.PENDING, initiated_at__lt=cutoff
    ).order_by("initiated_at")
    for payment in pending:
        try:
            result = get_provider(payment.method).check_status(payment)
        except Exception as exc:  # noqa: BLE001
            logger.exception("payment %s reconcile error", payment.id)
            result = ProviderResult("PENDING", message=f"PROVIDER_ERROR: {exc}")
        if result.status == "PENDING" and payment.initiated_at < give_up:
            result = ProviderResult("FAILED", reference=result.reference, message="RECONCILE_TIMEOUT",
                                    response=result.response)
        complete_payment(payment, result)
        processed += 1
    return processed
