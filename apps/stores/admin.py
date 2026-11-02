from django.contrib import admin

from .models import Kiosk, Store


@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
    list_display = (
        "id", "name", "currency", "secondary_currency", "secondary_rate", "tax_rate", "default_weight_tolerance_g",
        "is_active",
    )


@admin.register(Kiosk)
class KioskAdmin(admin.ModelAdmin):
    list_display = ("id", "store", "name", "serial_number", "key_prefix", "is_active", "last_seen_at",
                    "app_version")
    readonly_fields = ("key_hash", "key_prefix", "app_version", "device_status")
