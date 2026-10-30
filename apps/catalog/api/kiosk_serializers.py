from rest_framework import serializers

from apps.catalog.models import Product


class KioskProductSerializer(serializers.ModelSerializer):
    tolerance_g = serializers.IntegerField(source="effective_tolerance_g", read_only=True)
    category = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id", "barcode", "name", "price", "expected_weight_g", "tolerance_g", "requires_weight_check",
            "has_eas_tag", "category",
        ]
        read_only_fields = fields

    def get_category(self, obj) -> str | None:
        return obj.category.name if obj.category_id else None
