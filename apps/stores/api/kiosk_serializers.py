from rest_framework import serializers

DEVICE_KEYS = ("scanner", "scale", "printer", "eas")
DEVICE_STATES = ("OK", "WARN", "FAULT", "OFF")


class DeviceStatusSerializer(serializers.Serializer):
    """Peripheral status map. Every key is optional; unknown keys are rejected."""

    scanner = serializers.ChoiceField(choices=DEVICE_STATES, required=False)
    scale = serializers.ChoiceField(choices=DEVICE_STATES, required=False)
    printer = serializers.ChoiceField(choices=DEVICE_STATES, required=False)
    eas = serializers.ChoiceField(choices=DEVICE_STATES, required=False)

    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown = sorted(set(data) - set(DEVICE_KEYS))
            if unknown:
                raise serializers.ValidationError({key: ["Unknown device."] for key in unknown})
        return super().to_internal_value(data)


class HeartbeatRequestSerializer(serializers.Serializer):
    app_version = serializers.CharField(max_length=32, required=False, allow_blank=True)
    devices = DeviceStatusSerializer(required=False)


class StaffVerifySerializer(serializers.Serializer):
    staff_code = serializers.CharField(max_length=32)
    pin = serializers.CharField(max_length=32, trim_whitespace=False)


class StaffIdentitySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    role = serializers.CharField()
    staff_code = serializers.CharField()
