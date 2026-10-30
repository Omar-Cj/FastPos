import uuid

from django.db import models


class Payment(models.Model):
    """One attempt to collect the money for an order. Several attempts may exist (Waafi failed,
    customer retried with E-Dahab); at most one is SUCCESS. Raw provider payloads are kept for
    disputes."""

    class Method(models.TextChoices):
        WAAFI = "WAAFI", "Waafi (Hormuud)"
        EDAHAB = "EDAHAB", "E-Dahab (Somtel)"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        SUCCESS = "SUCCESS", "Success"
        FAILED = "FAILED", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, related_name="payments")
    method = models.CharField(max_length=10, choices=Method.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3)
    customer_phone = models.CharField(max_length=20, help_text="Wallet number that receives the USSD prompt.")

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    failure_reason = models.CharField(max_length=255, blank=True)
    provider_reference = models.CharField(max_length=128, blank=True, db_index=True,
                                          help_text="Provider transaction / invoice id.")
    provider_request = models.JSONField(null=True, blank=True)
    provider_response = models.JSONField(null=True, blank=True)

    client_request_id = models.UUIDField(null=True, blank=True, help_text="Idempotency-Key from the kiosk.")
    initiated_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["order", "client_request_id"],
                name="uniq_payment_client_request_per_order"),
        ]
        indexes = [models.Index(fields=["status", "initiated_at"])]
        ordering = ["-initiated_at"]

    def __str__(self):
        return f"{self.method} {self.amount} {self.currency} ({self.status})"
