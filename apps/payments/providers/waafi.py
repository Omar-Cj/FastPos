"""WaafiPay (Hormuud EVC Plus / Zaad / Sahal) merchant API adapter.

One synchronous `API_PURCHASE` call pushes a USSD prompt to the customer's phone; WaafiPay holds the
HTTP request open until the customer approves, declines, or the prompt expires.

NOTE: the purchase payload follows the public WaafiPay integration guide. The status-check call
(`check_status`) is best effort: the service name and response fields below MUST be confirmed
against the merchant documentation issued with the production credentials.
"""

from __future__ import annotations

import logging
import uuid

import requests
from django.conf import settings
from django.utils import timezone

from .base import ProviderError, ProviderResult, redact

logger = logging.getLogger(__name__)

SUCCESS_CODE = "2001"


class WaafiProvider:
    name = "waafi"

    def __init__(self, *, url: str | None = None, timeout: float | None = None):
        self.url = url or settings.WAAFI_API_URL
        self.timeout = timeout if timeout is not None else settings.PAYMENT_HTTP_TIMEOUT_SECONDS

    # --- payload helpers --------------------------------------------------
    def _envelope(self, service_name: str, params: dict) -> dict:
        return {
            "schemaVersion": "1.0",
            "requestId": str(uuid.uuid4()),
            "timestamp": timezone.now().isoformat(),
            "channelName": "WEB",
            "serviceName": service_name,
            "serviceParams": {
                "merchantUid": settings.WAAFI_MERCHANT_UID,
                "apiUserId": settings.WAAFI_API_USER_ID,
                "apiKey": settings.WAAFI_API_KEY,
                **params,
            },
        }

    def _post(self, body: dict) -> dict:
        logger.info("waafi request %s", redact(body))
        resp = requests.post(self.url, json=body, timeout=self.timeout)
        try:
            data = resp.json()
        except ValueError as exc:
            raise ProviderError(f"Waafi returned non-JSON (HTTP {resp.status_code})") from exc
        logger.info("waafi response %s", data)
        return data

    @staticmethod
    def invoice_id(payment) -> str:
        order = payment.order
        if order.receipt_number:
            return str(order.receipt_number)
        return order.id.hex[:12].upper()

    # --- contract ---------------------------------------------------------
    def charge(self, payment) -> ProviderResult:
        body = self._envelope("API_PURCHASE", {
            "paymentMethod": "MWALLET_ACCOUNT",
            "payerInfo": {"accountNo": payment.customer_phone},
            "transactionInfo": {
                "referenceId": str(payment.id),
                "invoiceId": self.invoice_id(payment),
                "amount": str(payment.amount),
                "currency": payment.currency,
                "description": "FastPOS order",
            },
        })
        stored_request = redact(body)
        try:
            data = self._post(body)
        except requests.Timeout:
            return ProviderResult("FAILED", message="TIMEOUT", request=stored_request)
        except requests.RequestException as exc:
            raise ProviderError(f"Waafi unreachable: {exc.__class__.__name__}") from exc

        params = data.get("params") or {}
        if str(data.get("responseCode")) == SUCCESS_CODE:
            return ProviderResult("SUCCESS", reference=str(params.get("transactionId", "")),
                                  message=data.get("responseMsg", ""), request=stored_request, response=data)
        return ProviderResult("FAILED", reference=str(params.get("transactionId", "") or ""),
                              message=data.get("responseMsg") or "Payment declined",
                              request=stored_request, response=data)

    def check_status(self, payment) -> ProviderResult:
        # TODO(confirm with merchant docs): service name, params and the "state" field are assumptions.
        body = self._envelope("API_GETTRANSACTIONSTATUS", {"referenceId": str(payment.id)})
        stored_request = redact(body)
        try:
            data = self._post(body)
        except requests.RequestException:
            # Unknown: keep it pending, the next reconcile run retries.
            return ProviderResult("PENDING", message="STATUS_CHECK_UNAVAILABLE", request=stored_request)

        params = data.get("params") or {}
        state = str(params.get("state", "")).upper()
        if str(data.get("responseCode")) == SUCCESS_CODE and state in ("", "APPROVED", "SUCCESS"):
            return ProviderResult("SUCCESS", reference=str(params.get("transactionId", "")),
                                  message=data.get("responseMsg", ""), request=stored_request, response=data)
        if state in ("PENDING", "INITIATED"):
            return ProviderResult("PENDING", message=state, request=stored_request, response=data)
        return ProviderResult("FAILED", message=data.get("responseMsg") or "Not paid",
                              request=stored_request, response=data)
