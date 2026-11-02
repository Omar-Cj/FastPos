import hashlib
import secrets

from django.db import models

from apps.core.models import TimeStampedModel


class Store(TimeStampedModel):
    """One supermarket. Every tenant row (kiosks, staff, products, orders) hangs off a store."""

    name = models.CharField(max_length=120)
    address = models.CharField(max_length=255, blank=True)
    currency = models.CharField(max_length=3, default="USD")
    secondary_currency = models.CharField(
        max_length=4, null=True, blank=True, help_text="Optional second currency shown on the kiosk, e.g. SLSH."
    )
    secondary_rate = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True,
        help_text="Units of secondary currency per 1 unit of the main currency.",
    )
    timezone = models.CharField(max_length=64, default="Africa/Mogadishu")
    tax_rate = models.DecimalField(max_digits=5, decimal_places=4, default=0, help_text="0.05 = 5%")
    default_weight_tolerance_g = models.PositiveIntegerField(
        default=10, help_text="Used when a product has no tolerance of its own."
    )
    next_receipt_number = models.PositiveIntegerField(default=1, help_text="Per-store gapless counter.")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Kiosk(TimeStampedModel):
    """A physical self-checkout machine. Authenticates with a device token that is stored hashed."""

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="kiosks")
    name = models.CharField(max_length=120)
    serial_number = models.CharField(max_length=120, unique=True, null=True, blank=True)
    key_hash = models.CharField(max_length=64, unique=True, editable=False)
    key_prefix = models.CharField(max_length=8, editable=False)
    is_active = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    app_version = models.CharField(max_length=32, blank=True, help_text="Reported by the kiosk on heartbeat.")
    device_status = models.JSONField(
        default=dict, blank=True, help_text='Last reported peripheral status, e.g. {"scanner": "OK"}.'
    )

    def __str__(self):
        return f"{self.store.name} / {self.name}"

    # --- token handling -------------------------------------------------
    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def issue_token(self) -> str:
        """Generate a new device token, store its hash, return the plain token (shown once)."""
        token = secrets.token_urlsafe(32)
        self.key_hash = self.hash_token(token)
        self.key_prefix = token[:8]
        return token

    @classmethod
    def authenticate_token(cls, token: str) -> "Kiosk | None":
        return (
            cls.objects.select_related("store")
            .filter(key_hash=cls.hash_token(token), is_active=True, store__is_active=True)
            .first()
        )
