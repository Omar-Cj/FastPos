"""eDahab (Somtel) merchant API adapter.

`IssueInvoice` pushes a payment request to the customer's eDahab wallet. Each call is signed with
`?hash=sha256(<exact JSON body> + EDAHAB_SECRET)`, so the body is serialised once and sent verbatim.

NOTE: endpoint names and response fields follow the eDahab merchant integration guide we have;
confirm `CheckInvoiceStatus` fields and status strings against the docs issued with production
credentials.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time

import requests
from django.conf import settings

from .base import ProviderError, ProviderResult, redact

logger = logging.getLogger(__name__)


class EdahabProvider:
    name = "edahab"
    poll_interval_seconds = 3.0

    def __init__(self, *, base_url: str | None = None, timeout: float | None = None):
        self.base_url = (base_url or settings.EDAHAB_API_URL).rstrip("/")
        self.timeout = timeout if timeout is not None else settings.PAYMENT_HTTP_TIMEOUT_SECONDS

    @staticmethod
    def sign(body_json: str) -> str:
        return hashlib.sha256((body_json + settings.EDAHAB_SECRET).encode()).hexdigest()

    def _post(self, endpoint: str, body: dict, timeout: float) -> dict:
        body_json = json.dumps(body, separators=(",", ":"))
        url = f"{self.base_url}/{endpoint}"
        logger.info("edahab %s request %s", endpoint, redact(body))
        resp = requests.post(url, params={"hash": self.sign(body_json)}, data=body_json,
                             headers={"Content-Type": "application/json"}, timeout=timeout)
        try:
            data = resp.json()
        except ValueError as exc:
            raise ProviderError(f"eDahab returned non-JSON (HTTP {resp.status_code})") from exc
        logger.info("edahab %s response %s", endpoint, data)
        return data

    @staticmethod
    def _interpret(data: dict, request: dict) -> ProviderResult:
        status = str(data.get("InvoiceStatus", ""))
        ref = str(data.get("TransactionId") or data.get("InvoiceId") or "")
        ok = data.get("StatusCode") in (0, "0")
        if ok and status == "Paid":
            return ProviderResult("SUCCESS", reference=ref, message=status, request=request, response=data)
        if ok and status == "Pending":
            return ProviderResult("PENDING", reference=ref, message=status, request=request, response=data)
        message = data.get("StatusDescription") or status or "Payment declined"
        return ProviderResult("FAILED", reference=ref, message=str(message), request=request, response=data)

    def _check(self, invoice_id, timeout: float) -> dict:
        return self._post("CheckInvoiceStatus", {"invoiceId": invoice_id, "apiKey": settings.EDAHAB_API_KEY},
                          timeout)

    def charge(self, payment) -> ProviderResult:
        deadline = time.monotonic() + self.timeout
        body = {
            "apiKey": settings.EDAHAB_API_KEY,
            "edahabNumber": payment.customer_phone,
            "amount": float(payment.amount),
            "agentCode": settings.EDAHAB_AGENT_CODE,
            "currency": payment.currency,
            "ReturnUrl": "",
        }
        stored_request = redact(body)
        try:
            data = self._post("IssueInvoice", body, self.timeout)
        except requests.Timeout:
            return ProviderResult("FAILED", message="TIMEOUT", request=stored_request)
        except requests.RequestException as exc:
            raise ProviderError(f"eDahab unreachable: {exc.__class__.__name__}") from exc

        result = self._interpret(data, stored_request)
        invoice_id = data.get("InvoiceId")
        while result.status == "PENDING" and invoice_id:
            remaining = deadline - time.monotonic()
            if remaining <= self.poll_interval_seconds:
                break
            time.sleep(self.poll_interval_seconds)
            try:
                polled = self._check(invoice_id, max(1.0, deadline - time.monotonic()))
            except requests.RequestException:
                continue
            result = self._interpret(polled, stored_request)
            result.reference = result.reference or str(invoice_id)
        if result.status == "PENDING":
            # Customer has not answered within our budget; the reconcile task settles it later.
            result.reference = result.reference or str(invoice_id or "")
        return result

    def check_status(self, payment) -> ProviderResult:
        invoice_id = self._invoice_id_of(payment)
        if not invoice_id:
            return ProviderResult("FAILED", message="NO_INVOICE")
        try:
            data = self._check(invoice_id, self.timeout)
        except requests.RequestException:
            return ProviderResult("PENDING", message="STATUS_CHECK_UNAVAILABLE")
        result = self._interpret(data, {"invoiceId": invoice_id, "apiKey": "***"})
        result.reference = result.reference or str(invoice_id)
        return result

    @staticmethod
    def _invoice_id_of(payment):
        response = payment.provider_response or {}
        return response.get("InvoiceId") or payment.provider_reference or None
