from __future__ import annotations

from django.conf import settings

from .base import PaymentProvider
from .edahab import EdahabProvider
from .mock import MockProvider
from .waafi import WaafiProvider

_PROVIDERS = {"WAAFI": WaafiProvider, "EDAHAB": EdahabProvider}


def get_provider(method: str) -> PaymentProvider:
    if settings.PAYMENT_PROVIDER_BACKEND == "mock":
        return MockProvider()
    try:
        return _PROVIDERS[method]()
    except KeyError as exc:
        raise ValueError(f"Unknown payment method {method!r}") from exc
