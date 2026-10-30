from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import AbstractUser
from django.db import models


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create(self, email, password, **extra):
        if not email:
            raise ValueError("Email is required")
        user = self.model(email=self.normalize_email(email), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("role", User.Role.ADMIN)
        return self._create(email, password, **extra)


class User(AbstractUser):
    """ADMIN uses the React panel (JWT). STAFF only exists to type a code + PIN on a kiosk.

    Superusers (`store` is null) are platform operators and can see every store.
    """

    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Store admin"
        STAFF = "STAFF", "Floor staff"

    username = None
    email = models.EmailField(unique=True)
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.STAFF)
    store = models.ForeignKey(
        "stores.Store", on_delete=models.CASCADE, related_name="users", null=True, blank=True
    )
    staff_code = models.CharField(max_length=20, null=True, blank=True, help_text="Typed on the kiosk before the PIN.")
    pin_hash = models.CharField(max_length=128, blank=True, editable=False)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    objects = UserManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["store", "staff_code"], name="uniq_staff_code_per_store"),
        ]

    def __str__(self):
        return self.email

    def save(self, *args, **kwargs):
        if self.staff_code == "":
            self.staff_code = None  # keep the unique constraint meaningful on MySQL
        super().save(*args, **kwargs)

    # --- PIN ---------------------------------------------------------------
    def set_pin(self, raw_pin: str) -> None:
        self.pin_hash = make_password(raw_pin)

    def check_pin(self, raw_pin: str) -> bool:
        return bool(self.pin_hash) and check_password(raw_pin, self.pin_hash)
