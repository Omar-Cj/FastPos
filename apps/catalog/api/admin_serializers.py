from rest_framework import serializers

from apps.catalog.models import Category, Product

from .kiosk_serializers import absolute_image_url


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "name_so", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_name(self, value):
        value = value.strip()
        qs = Category.objects.filter(store_id=self.context["store_id"], name__iexact=value)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("A category with this name already exists in this store.")
        return value


class ProductSerializer(serializers.ModelSerializer):
    category = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.all(), allow_null=True, required=False
    )
    category_name = serializers.CharField(source="category.name", read_only=True, default=None)
    effective_tolerance_g = serializers.IntegerField(read_only=True)
    price = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    image = serializers.ImageField(required=False, allow_null=True, write_only=True)
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id", "barcode", "name", "name_so", "category", "category_name", "price", "expected_weight_g",
            "weight_tolerance_g", "effective_tolerance_g", "requires_weight_check", "has_eas_tag",
            "is_active", "image", "image_url", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_image_url(self, obj) -> str | None:
        return absolute_image_url(obj, self.context.get("request"))

    def validate_category(self, value):
        if value is not None and value.store_id != self.context["store_id"]:
            raise serializers.ValidationError("Category belongs to another store.")
        return value

    def validate_barcode(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Barcode is required.")
        qs = Product.objects.filter(store_id=self.context["store_id"], barcode=value)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("A product with this barcode already exists in this store.")
        return value


class ImportResultErrorSerializer(serializers.Serializer):
    row = serializers.IntegerField()
    barcode = serializers.CharField()
    message = serializers.CharField()


class ImportResultSerializer(serializers.Serializer):
    created = serializers.IntegerField()
    updated = serializers.IntegerField()
    errors = ImportResultErrorSerializer(many=True)


class ImportRequestSerializer(serializers.Serializer):
    file = serializers.FileField(
        help_text="CSV: barcode,name,price,expected_weight_g,weight_tolerance_g,category,name_so"
    )
