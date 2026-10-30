import re

from rest_framework import serializers

from ..models import Payment

PHONE_RE = re.compile(r"^\+?\d{7,15}$")


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ["id", "order", "method", "amount", "currency", "customer_phone", "status",
                  "failure_reason", "provider_reference", "initiated_at", "completed_at"]
        read_only_fields = fields


class PaymentStatusSerializer(PaymentSerializer):
    order_status = serializers.CharField(source="order.status", read_only=True)

    class Meta(PaymentSerializer.Meta):
        fields = [*PaymentSerializer.Meta.fields, "order_status"]
        read_only_fields = fields


class InitiatePaymentSerializer(serializers.Serializer):
    method = serializers.ChoiceField(choices=Payment.Method.choices)
    customer_phone = serializers.CharField(max_length=20)

    def validate_customer_phone(self, value):
        value = value.strip().replace(" ", "")
        if not PHONE_RE.match(value):
            raise serializers.ValidationError("Phone must be 7-15 digits with an optional leading +.")
        return value


class OrderEventSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=["RECEIPT_PRINTED", "EAS_DEACTIVATED"])


class OrderEventResponseSerializer(serializers.Serializer):
    order_id = serializers.UUIDField()
    type = serializers.CharField()
    recorded_at = serializers.DateTimeField()


class ReceiptLineSerializer(serializers.Serializer):
    barcode = serializers.CharField()
    name = serializers.CharField()
    quantity = serializers.IntegerField()
    unit_price = serializers.DecimalField(max_digits=12, decimal_places=2)
    line_total = serializers.DecimalField(max_digits=12, decimal_places=2)


class ReceiptStoreSerializer(serializers.Serializer):
    name = serializers.CharField()
    address = serializers.CharField()


class ReceiptPaymentSerializer(serializers.Serializer):
    method = serializers.CharField()
    provider_reference = serializers.CharField()
    customer_phone = serializers.CharField()
    completed_at = serializers.DateTimeField(allow_null=True)


class ReceiptSerializer(serializers.Serializer):
    """Documentation-only shape of `build_receipt()`."""

    receipt_number = serializers.IntegerField()
    order_id = serializers.UUIDField()
    store = ReceiptStoreSerializer()
    kiosk = serializers.CharField()
    paid_at = serializers.DateTimeField()
    currency = serializers.CharField()
    lines = ReceiptLineSerializer(many=True)
    subtotal = serializers.DecimalField(max_digits=12, decimal_places=2)
    tax = serializers.DecimalField(max_digits=12, decimal_places=2)
    total = serializers.DecimalField(max_digits=12, decimal_places=2)
    payment = ReceiptPaymentSerializer(allow_null=True)
    item_count = serializers.IntegerField()
