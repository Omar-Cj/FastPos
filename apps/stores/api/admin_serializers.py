from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

from apps.stores.models import Kiosk, Store

ONLINE_WINDOW = timedelta(minutes=2)


class StoreSerializer(serializers.ModelSerializer):
    class Meta:
        model = Store
        fields = [
            "id", "name", "address", "currency", "timezone", "tax_rate", "default_weight_tolerance_g",
            "is_active", "next_receipt_number", "created_at",
        ]
        read_only_fields = ["id", "next_receipt_number", "created_at"]

    def validate_tax_rate(self, value):
        if value < 0 or value >= 1:
            raise serializers.ValidationError("Tax rate must be a fraction between 0 and 1 (0.05 = 5%).")
        return value

    def validate_timezone(self, value):
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise serializers.ValidationError("Unknown timezone.") from exc
        return value


class KioskSerializer(serializers.ModelSerializer):
    is_online = serializers.SerializerMethodField()

    class Meta:
        model = Kiosk
        fields = [
            "id", "store", "name", "serial_number", "is_active", "key_prefix", "last_seen_at", "is_online",
            "created_at",
        ]
        read_only_fields = ["id", "store", "key_prefix", "last_seen_at", "created_at"]

    def get_is_online(self, obj) -> bool:
        return bool(obj.last_seen_at and timezone.now() - obj.last_seen_at <= ONLINE_WINDOW)


class KioskTokenSerializer(serializers.Serializer):
    token = serializers.CharField(help_text="Plain device token. Shown once; only its hash is stored.")
