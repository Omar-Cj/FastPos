"""Provider contract shared by every mobile-money adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

REDACTED = "***"


@dataclass
class ProviderResult:
    """Outcome of one provider call.

    `request`/`response` are the (redacted) raw payloads; they are stored on the Payment for disputes.
    """

    status: Literal["SUCCESS", "FAILED", "PENDING"]
    reference: str = ""
    message: str = ""
    request: dict | None = None
    response: dict | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in ("SUCCESS", "FAILED")


class ProviderError(Exception):
    """Unexpected provider behaviour (bad JSON, HTTP 5xx...). Tasks turn it into a FAILED payment."""


class PaymentProvider(Protocol):
    def charge(self, payment) -> ProviderResult:
        """Push the USSD prompt and block until the customer approves, declines or it times out."""
        ...

    def check_status(self, payment) -> ProviderResult:
        """Ask the provider what happened to an earlier charge (reconciliation)."""
        ...


def redact(data: dict, keys: tuple[str, ...] = ("apiKey",)) -> dict:
    """Deep-copy `data` replacing secret values with ***. Never store or log an unredacted payload."""
    if isinstance(data, dict):
        return {k: (REDACTED if k in keys else redact(v, keys)) for k, v in data.items()}
    if isinstance(data, list):
        return [redact(v, keys) for v in data]
    return data
