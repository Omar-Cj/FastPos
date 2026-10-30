import uuid

from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class Order(TimeStampedModel):
    """One customer's basket. Becomes the receipt once PAID.

    OPEN -> AWAITING_PAYMENT -> PAID
      |            |
      |            +--(payment failed)--> OPEN
      +--> CANCELLED
    Items may only change while OPEN.
    """

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        AWAITING_PAYMENT = "AWAITING_PAYMENT", "Awaiting payment"
        PAID = "PAID", "Paid"
        CANCELLED = "CANCELLED", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey("stores.Store", on_delete=models.PROTECT, related_name="orders")
    kiosk = models.ForeignKey("stores.Kiosk", on_delete=models.PROTECT, related_name="orders")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)

    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    receipt_number = models.PositiveIntegerField(null=True, blank=True, help_text="Assigned on PAID.")
    paid_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=255, blank=True)
    receipt_printed_at = models.DateTimeField(null=True, blank=True)
    eas_deactivated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["store", "status", "created_at"])]
        constraints = [
            models.UniqueConstraint(fields=["store", "receipt_number"], name="uniq_receipt_number_per_store"),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"Order {self.id} ({self.status})"

    @property
    def is_open(self) -> bool:
        return self.status == self.Status.OPEN


class WeightVerification(models.Model):
    """Black-box recorder: one row per weigh attempt, pass or fail.

    `result` is the verdict of the arithmetic. `outcome` is what happened next:
    a PASS is ADDED; a FAIL starts REJECTED and flips to OVERRIDDEN if staff approve it.
    """

    class Result(models.TextChoices):
        PASS = "PASS", "Pass"
        FAIL = "FAIL", "Fail"

    class Outcome(models.TextChoices):
        ADDED = "ADDED", "Added to order"
        REJECTED = "REJECTED", "Rejected"
        OVERRIDDEN = "OVERRIDDEN", "Overridden by staff"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="verifications")
    kiosk = models.ForeignKey("stores.Kiosk", on_delete=models.PROTECT, related_name="verifications")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="verifications")

    expected_weight_g = models.PositiveIntegerField()
    tolerance_g = models.PositiveIntegerField()
    measured_weight_g = models.PositiveIntegerField()
    delta_g = models.IntegerField(help_text="measured - expected")

    result = models.CharField(max_length=4, choices=Result.choices)
    outcome = models.CharField(max_length=10, choices=Outcome.choices)
    override_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="overrides"
    )
    override_reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["kiosk", "created_at"]),
            models.Index(fields=["result", "created_at"]),
        ]
        ordering = ["-created_at"]


class OrderItem(models.Model):
    """One physical unit in the basket. Fields are snapshots taken at scan time so a later price
    or weight edit never rewrites an old receipt. A failed weigh never becomes an item."""

    class WeightStatus(models.TextChoices):
        PASSED = "PASSED", "Passed"
        OVERRIDDEN = "OVERRIDDEN", "Overridden by staff"
        SKIPPED = "SKIPPED", "Weight check not required"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="order_items")

    barcode = models.CharField(max_length=64)
    product_name = models.CharField(max_length=200)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    expected_weight_g = models.PositiveIntegerField()
    tolerance_g = models.PositiveIntegerField()
    measured_weight_g = models.PositiveIntegerField(null=True, blank=True)

    weight_status = models.CharField(max_length=10, choices=WeightStatus.choices)
    verification = models.OneToOneField(
        WeightVerification, on_delete=models.SET_NULL, null=True, blank=True, related_name="item"
    )
    overridden_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="overridden_items"
    )
    client_request_id = models.UUIDField(null=True, blank=True, help_text="Idempotency-Key from the kiosk.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["order", "client_request_id"], name="uniq_item_client_request_per_order"),
        ]
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.product_name} x1 @ {self.unit_price}"
