import re

from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from apps.accounts.models import User

PIN_RE = re.compile(r"^\d{4,6}$")


def validate_pin_value(value: str) -> str:
    if not PIN_RE.match(value or ""):
        raise serializers.ValidationError("PIN must be 4 to 6 digits.")
    return value


class MeSerializer(serializers.ModelSerializer):
    store = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "email", "role", "store", "is_superuser"]
        read_only_fields = fields

    def get_store(self, obj) -> dict | None:
        if obj.store_id is None:
            return None
        return {"id": obj.store_id, "name": obj.store.name}


class StaffSerializer(serializers.ModelSerializer):
    """Users of the admin's store. `store_id` comes from the view via context."""

    role = serializers.ChoiceField(choices=User.Role.choices, default=User.Role.STAFF)
    has_pin = serializers.SerializerMethodField()
    created = serializers.DateTimeField(source="date_joined", read_only=True)
    password = serializers.CharField(write_only=True, required=False, trim_whitespace=False,
                                     style={"input_type": "password"})
    pin = serializers.CharField(write_only=True, required=False, allow_blank=True, trim_whitespace=False)

    class Meta:
        model = User
        fields = [
            "id", "email", "first_name", "last_name", "role", "staff_code", "is_active", "has_pin", "created",
            "password", "pin",
        ]
        read_only_fields = ["id"]

    def get_has_pin(self, obj) -> bool:
        return bool(obj.pin_hash)

    def validate_pin(self, value):
        if value == "":
            return value
        return validate_pin_value(value)

    def validate_password(self, value):
        validate_password(value)
        return value

    def validate_staff_code(self, value):
        if not value:
            return None
        qs = User.objects.filter(store_id=self.context["store_id"], staff_code=value)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("This staff code is already used in this store.")
        return value

    def validate(self, attrs):
        if self.instance is None and not attrs.get("password"):
            raise serializers.ValidationError({"password": ["This field is required."]})
        return attrs

    def _apply_secrets(self, user, password, pin):
        if password:
            user.set_password(password)
        if pin:
            user.set_pin(pin)

    def create(self, validated_data):
        password = validated_data.pop("password")
        pin = validated_data.pop("pin", "")
        user = User(**validated_data)
        self._apply_secrets(user, password, pin)
        user.save()
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        pin = validated_data.pop("pin", "")
        for key, value in validated_data.items():
            setattr(instance, key, value)
        self._apply_secrets(instance, password, pin)
        instance.save()
        return instance


class SetPinSerializer(serializers.Serializer):
    pin = serializers.CharField(trim_whitespace=False)

    def validate_pin(self, value):
        return validate_pin_value(value)
