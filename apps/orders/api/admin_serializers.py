from rest_framework import serializers

from apps.orders.models import KioskEvent, Order, OrderItem, WeightVerification
from apps.payments.models import Payment


class AdminOrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = [
            "id", "product", "barcode", "product_name", "unit_price", "expected_weight_g", "tolerance_g",
            "measured_weight_g", "weight_status", "verification", "overridden_by", "created_at",
        ]
        read_only_fields = fields


class WeightVerificationSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    barcode = serializers.CharField(source="product.barcode", read_only=True)

    class Meta:
        model = WeightVerification
        fields = [
            "id", "order", "kiosk", "product", "product_name", "barcode", "expected_weight_g",
            "tolerance_g", "measured_weight_g", "delta_g", "result", "outcome", "override_by",
            "override_reason", "created_at",
        ]
        read_only_fields = fields


class OrderPaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = [
            "id", "method", "amount", "currency", "status", "provider_reference", "customer_phone",
            "initiated_at", "completed_at",
        ]
        read_only_fields = fields


class AdminOrderListSerializer(serializers.ModelSerializer):
    kiosk_name = serializers.CharField(source="kiosk.name", read_only=True)
    item_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Order
        fields = [
            "id", "kiosk", "kiosk_name", "status", "subtotal", "tax", "total", "receipt_number",
            "item_count", "created_at", "paid_at",
        ]
        read_only_fields = fields


class AdminOrderDetailSerializer(AdminOrderListSerializer):
    items = AdminOrderItemSerializer(many=True, read_only=True)
    verifications = WeightVerificationSerializer(many=True, read_only=True)
    payments = OrderPaymentSerializer(many=True, read_only=True)

    class Meta(AdminOrderListSerializer.Meta):
        fields = AdminOrderListSerializer.Meta.fields + [
            "updated_at", "cancelled_at", "cancel_reason", "receipt_printed_at", "eas_deactivated_at",
            "items", "verifications", "payments",
        ]
        read_only_fields = fields


class KioskEventAdminSerializer(serializers.ModelSerializer):
    kiosk_name = serializers.CharField(source="kiosk.name", read_only=True)

    class Meta:
        model = KioskEvent
        fields = ["id", "kiosk", "kiosk_name", "order", "type", "detail", "created_at"]
        read_only_fields = fields
