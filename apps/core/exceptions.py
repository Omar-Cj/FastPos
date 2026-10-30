"""Uniform error envelope for every API response.

Every error looks like:
    {"error": {"code": "WEIGHT_MISMATCH", "message": "...", "details": {...}}}

Raise `APIError` from services/views with a stable machine-readable `code`; the Android and
React clients switch on the code, never on the message.
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler


class APIError(exceptions.APIException):
    """Business error with a stable code. Subclass or instantiate directly."""

    status_code = status.HTTP_400_BAD_REQUEST
    code = "ERROR"
    message = "Request failed."

    def __init__(self, message: str | None = None, *, code: str | None = None,
                 details: dict | None = None, status_code: int | None = None):
        self.code = code or self.code
        self.message = message or self.message
        self.details = details or {}
        if status_code is not None:
            self.status_code = status_code
        super().__init__(detail=self.message, code=self.code)


class NotFound(APIError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "NOT_FOUND"
    message = "Not found."


class Conflict(APIError):
    status_code = status.HTTP_409_CONFLICT
    code = "CONFLICT"
    message = "Conflict."


class Unprocessable(APIError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "UNPROCESSABLE"
    message = "Unprocessable."


# Domain error codes used across apps. Keep this list in sync with the API docs.
class ProductNotFound(NotFound):
    code = "PRODUCT_NOT_FOUND"
    message = "No active product with this barcode in this store."


class OrderNotOpen(Conflict):
    code = "ORDER_NOT_OPEN"
    message = "Order is not open for changes."


class OrderNotPaid(Conflict):
    code = "ORDER_NOT_PAID"
    message = "Order has not been paid."


class EmptyOrder(APIError):
    code = "EMPTY_ORDER"
    message = "Order has no items."


class WeightMismatch(Unprocessable):
    code = "WEIGHT_MISMATCH"
    message = "Measured weight does not match the expected weight."


class InvalidStaffPin(APIError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "INVALID_STAFF_PIN"
    message = "Staff code or PIN is invalid."


class PaymentInProgress(Conflict):
    code = "PAYMENT_IN_PROGRESS"
    message = "A payment is already in progress for this order."


class PaymentFailed(APIError):
    status_code = status.HTTP_402_PAYMENT_REQUIRED
    code = "PAYMENT_FAILED"
    message = "Payment failed."


def _envelope(code: str, message: str, details=None, http_status: int = 400) -> Response:
    return Response({"error": {"code": code, "message": message, "details": details or {}}}, status=http_status)


def api_exception_handler(exc, context):
    """Convert every exception DRF knows about into the envelope."""
    if isinstance(exc, APIError):
        return _envelope(exc.code, exc.message, exc.details, exc.status_code)

    if isinstance(exc, Http404):
        return _envelope("NOT_FOUND", "Not found.", None, status.HTTP_404_NOT_FOUND)
    if isinstance(exc, DjangoPermissionDenied):
        return _envelope("PERMISSION_DENIED", "Permission denied.", None, status.HTTP_403_FORBIDDEN)

    response = drf_exception_handler(exc, context)
    if response is None:
        return None  # let Django render a 500

    if isinstance(exc, exceptions.ValidationError):
        return _envelope("VALIDATION_ERROR", "Invalid input.", exc.detail, response.status_code)
    if isinstance(exc, exceptions.NotAuthenticated | exceptions.AuthenticationFailed):
        return _envelope("AUTHENTICATION_FAILED", str(exc.detail), None, response.status_code)
    if isinstance(exc, exceptions.PermissionDenied):
        return _envelope("PERMISSION_DENIED", str(exc.detail), None, response.status_code)
    if isinstance(exc, exceptions.NotFound):
        return _envelope("NOT_FOUND", str(exc.detail), None, response.status_code)
    if isinstance(exc, exceptions.Throttled):
        return _envelope("THROTTLED", str(exc.detail), None, response.status_code)

    code = getattr(exc, "default_code", "error")
    return _envelope(str(code).upper(), str(getattr(exc, "detail", exc)), None, response.status_code)
