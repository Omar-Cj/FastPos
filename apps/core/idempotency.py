"""Kiosks retry on flaky wifi. A client-generated `Idempotency-Key` header (UUID) lets the server
return the already-created row instead of creating a duplicate."""

import uuid

from rest_framework.exceptions import ValidationError

HEADER = "Idempotency-Key"


def get_idempotency_key(request) -> uuid.UUID | None:
    raw = request.headers.get(HEADER)
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise ValidationError({HEADER: ["Must be a UUID."]}) from exc
