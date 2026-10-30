from rest_framework import serializers

from apps.orders.models import Order, OrderItem


class OrderItemSerializer(serializers.ModelSerializer):
    product_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = OrderItem
        fields = [
            "id", "product_id", "barcode", "product_name", "unit_price", "expected_weight_g",
            "tolerance_g", "measured_weight_g", "weight_status", "created_at",
        ]
        read_only_fields = fields


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    item_count = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id", "status", "kiosk", "subtotal", "tax", "total", "item_count", "items",
            "receipt_number", "created_at", "updated_at", "paid_at", "cancelled_at", "cancel_reason",
        ]
        read_only_fields = fields

    def get_item_count(self, obj) -> int:
        return len(obj.items.all())


class OrderTotalsSerializer(serializers.Serializer):
    subtotal = serializers.DecimalField(max_digits=12, decimal_places=2)
    tax = serializers.DecimalField(max_digits=12, decimal_places=2)
    total = serializers.DecimalField(max_digits=12, decimal_places=2)
    item_count = serializers.IntegerField()

    @staticmethod
    def for_order(order: Order) -> dict:
        return OrderTotalsSerializer({
            "subtotal": order.subtotal, "tax": order.tax, "total": order.total,
            "item_count": order.items.count(),
        }).data


class ItemResultSerializer(serializers.Serializer):
    """Response of add / override."""

    item = OrderItemSerializer()
    order = OrderTotalsSerializer()


class TotalsResultSerializer(serializers.Serializer):
    order = OrderTotalsSerializer()


# --- request bodies ------------------------------------------------------------------------------

class AddItemSerializer(serializers.Serializer):
    barcode = serializers.CharField(max_length=64)
    measured_weight_g = serializers.IntegerField(min_value=0, required=False, allow_null=True)


class StaffCredentialsSerializer(serializers.Serializer):
    staff_code = serializers.CharField(max_length=32)
    pin = serializers.CharField(max_length=32, trim_whitespace=False)


class OverrideItemSerializer(StaffCredentialsSerializer):
    verification_id = serializers.IntegerField()
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


class CancelOrderSerializer(serializers.Serializer):
    staff_code = serializers.CharField(max_length=32, required=False, allow_blank=True)
    pin = serializers.CharField(max_length=32, required=False, allow_blank=True, trim_whitespace=False)
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
