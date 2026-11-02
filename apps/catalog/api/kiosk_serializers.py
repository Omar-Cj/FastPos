from rest_framework import serializers

from apps.catalog.models import Product


class KioskProductSerializer(serializers.ModelSerializer):
    tolerance_g = serializers.IntegerField(source="effective_tolerance_g", read_only=True)
    category = serializers.SerializerMethodField()
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id", "barcode", "name", "name_so", "price", "expected_weight_g", "tolerance_g", "requires_weight_check",
            "has_eas_tag", "category", "image_url",
        ]
        read_only_fields = fields

    def get_category(self, obj) -> str | None:
        return obj.category.name if obj.category_id else None

    def get_image_url(self, obj) -> str | None:
        return absolute_image_url(obj, self.context.get("request"))


def absolute_image_url(obj, request) -> str | None:
    """Absolute URL of the product image, or None when there is no image."""
    if not obj.image:
        return None
    url = obj.image.url
    return request.build_absolute_uri(url) if request is not None else url
