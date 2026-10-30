"""Deterministic provider for dev, tests and the simulator.

Outcome comes from `settings.PAYMENT_MOCK_OUTCOME` (success | fail | timeout), overridden per phone:
    phone ending 0000 -> fail
    phone ending 9999 -> timeout
"""

from __future__ import annotations

import time
import uuid

from django.conf import settings

from .base import ProviderResult

FAIL_SUFFIX = "0000"
TIMEOUT_SUFFIX = "9999"


def _outcome_for(phone: str) -> str:
    if phone.endswith(FAIL_SUFFIX):
        return "fail"
    if phone.endswith(TIMEOUT_SUFFIX):
        return "timeout"
    return getattr(settings, "PAYMENT_MOCK_OUTCOME", "success")


class MockProvider:
    name = "mock"

    def charge(self, payment) -> ProviderResult:
        outcome = _outcome_for(payment.customer_phone)
        delay = float(getattr(settings, "PAYMENT_MOCK_DELAY_SECONDS", 0) or 0)
        if delay > 0:
            time.sleep(delay)
        request = {"mock": True, "method": payment.method, "phone": payment.customer_phone,
                   "amount": str(payment.amount), "currency": payment.currency}
        if outcome == "success":
            ref = f"MOCK-{uuid.uuid4().hex[:12].upper()}"
            return ProviderResult("SUCCESS", reference=ref, message="Approved", request=request,
                                  response={"outcome": "success", "reference": ref})
        if outcome == "timeout":
            return ProviderResult("FAILED", message="TIMEOUT", request=request,
                                  response={"outcome": "timeout"})
        return ProviderResult("FAILED", message="Customer declined", request=request,
                              response={"outcome": "fail"})

    def check_status(self, payment) -> ProviderResult:
        phone = payment.customer_phone
        if phone.endswith(FAIL_SUFFIX) or phone.endswith(TIMEOUT_SUFFIX):
            return ProviderResult("FAILED", message="Not paid", response={"outcome": "fail"})
        ref = payment.provider_reference or f"MOCK-{uuid.uuid4().hex[:12].upper()}"
        return ProviderResult("SUCCESS", reference=ref, message="Approved", response={"outcome": "success"})
