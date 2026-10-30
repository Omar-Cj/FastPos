from django.contrib import admin

from .models import Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("id", "order", "method", "amount", "currency", "status", "provider_reference",
                    "initiated_at", "completed_at")
    list_filter = ("method", "status")
    search_fields = ("provider_reference", "customer_phone")
    readonly_fields = [f.name for f in Payment._meta.fields]
