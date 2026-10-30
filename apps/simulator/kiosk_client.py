"""Thin HTTP client for the kiosk API, used by `simulate_checkout` (and handy in a Python shell).

Every method returns `(status_code, json)`. Non-2xx responses raise `KioskAPIError`, except
422 WEIGHT_MISMATCH from `add_item`, which is a normal outcome the caller inspects.
"""

from __future__ import annotations

import uuid
from urllib.parse import quote

import requests

KIOSK_PREFIX = "/api/v1/kiosk/"


class KioskAPIError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None, status: int | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        self.status = status
        super().__init__(f"[{status}] {code}: {message}")


class KioskClient:
    def __init__(self, base_url: str, token: str, session: requests.Session | None = None,
                 timeout: float = 30):
        self.base_url = base_url.rstrip("/") + KIOSK_PREFIX
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": f"Kiosk {token}", "Accept": "application/json"})
        self.timeout = timeout

    # --- plumbing ----------------------------------------------------------------------------
    def _request(self, method: str, path: str, *, json=None, headers=None, allow_weight_mismatch=False):
        try:
            resp = self.session.request(method, self.base_url + path, json=json, headers=headers,
                                        timeout=self.timeout)
        except requests.RequestException as exc:
            raise KioskAPIError("NETWORK_ERROR", str(exc)) from exc
        try:
            body = resp.json() if resp.content else {}
        except ValueError:
            body = {"raw": resp.text[:500]}
        if 200 <= resp.status_code < 300:
            return resp.status_code, body
        error = body.get("error", {}) if isinstance(body, dict) else {}
        code = error.get("code") or f"HTTP_{resp.status_code}"
        if allow_weight_mismatch and resp.status_code == 422 and code == "WEIGHT_MISMATCH":
            return resp.status_code, body
        raise KioskAPIError(code, error.get("message") or resp.reason or "Request failed.",
                            error.get("details"), resp.status_code)

    @staticmethod
    def _idem() -> dict:
        return {"Idempotency-Key": str(uuid.uuid4())}

    # --- endpoints ---------------------------------------------------------------------------
    def heartbeat(self):
        return self._request("POST", "heartbeat/")

    def open_order(self):
        return self._request("POST", "orders/")

    def lookup(self, barcode: str):
        return self._request("GET", f"products/lookup/?barcode={quote(barcode)}")

    def add_item(self, order_id, barcode: str, measured_weight_g: int | None):
        return self._request("POST", f"orders/{order_id}/items/", headers=self._idem(),
                             json={"barcode": barcode, "measured_weight_g": measured_weight_g},
                             allow_weight_mismatch=True)

    def override(self, order_id, verification_id: int, staff_code: str, pin: str, reason: str = ""):
        return self._request("POST", f"orders/{order_id}/items/override/", json={
            "verification_id": verification_id, "staff_code": staff_code, "pin": pin, "reason": reason,
        })

    def remove_item(self, order_id, item_id: int, staff_code: str, pin: str):
        return self._request("DELETE", f"orders/{order_id}/items/{item_id}/",
                             json={"staff_code": staff_code, "pin": pin})

    def cancel(self, order_id, staff_code: str | None = None, pin: str | None = None, reason: str = ""):
        body = {"reason": reason}
        if staff_code:
            body.update(staff_code=staff_code, pin=pin or "")
        return self._request("POST", f"orders/{order_id}/cancel/", json=body)

    def pay(self, order_id, method: str, phone: str):
        return self._request("POST", f"orders/{order_id}/payments/", headers=self._idem(),
                             json={"method": method, "customer_phone": phone})

    def payment_status(self, order_id, payment_id):
        return self._request("GET", f"orders/{order_id}/payments/{payment_id}/")

    def receipt(self, order_id):
        return self._request("GET", f"orders/{order_id}/receipt/")

    def event(self, order_id, type_: str):
        return self._request("POST", f"orders/{order_id}/events/", json={"type": type_})
