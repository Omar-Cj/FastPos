from django.contrib import admin

from .models import Category, Product


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("id", "store", "name", "name_so")
    list_filter = ("store",)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("barcode", "name", "name_so", "store", "price", "expected_weight_g", "weight_tolerance_g",
                    "requires_weight_check", "has_eas_tag", "is_active")
    list_filter = ("store", "is_active", "requires_weight_check", "category")
    search_fields = ("barcode", "name", "name_so")
