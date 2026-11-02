from django.contrib import admin

from .models import KioskEvent, Order, OrderItem, WeightVerification


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    readonly_fields = ("product_name", "barcode", "unit_price", "expected_weight_g", "tolerance_g",
                       "measured_weight_g", "weight_status", "overridden_by")


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "store", "kiosk", "status", "total", "receipt_number", "created_at", "paid_at")
    list_filter = ("store", "status", "kiosk")
    inlines = [OrderItemInline]
    readonly_fields = [f.name for f in Order._meta.fields]


@admin.register(WeightVerification)
class WeightVerificationAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "kiosk", "product", "expected_weight_g", "measured_weight_g",
                    "delta_g", "tolerance_g", "result", "outcome", "override_by")
    list_filter = ("result", "outcome", "kiosk")
    readonly_fields = [f.name for f in WeightVerification._meta.fields]


@admin.register(KioskEvent)
class KioskEventAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "store", "kiosk", "type", "order")
    list_filter = ("store", "type", "kiosk")
    readonly_fields = [f.name for f in KioskEvent._meta.fields]
