from rest_framework import serializers

from ..models import Payment


class AdminPaymentSerializer(serializers.ModelSerializer):
    order_receipt_number = serializers.IntegerField(
        source="order.receipt_number", read_only=True, allow_null=True
    )

    class Meta:
        model = Payment
        fields = ["id", "order", "order_receipt_number", "method", "amount", "currency", "customer_phone",
                  "status", "failure_reason", "provider_reference", "initiated_at", "completed_at"]
        read_only_fields = fields
