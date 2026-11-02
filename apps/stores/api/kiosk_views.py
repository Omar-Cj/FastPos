"""Device-level kiosk endpoints."""

from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.services import verify_staff
from apps.core.mixins import KioskViewMixin
from apps.stores.models import Kiosk

from .kiosk_serializers import HeartbeatRequestSerializer, StaffIdentitySerializer, StaffVerifySerializer


class HeartbeatView(KioskViewMixin, APIView):
    @extend_schema(request=HeartbeatRequestSerializer, responses={200: OpenApiTypes.OBJECT},
                   summary="Kiosk heartbeat: marks the device online, records app/device status, "
                           "returns store settings")
    def post(self, request):
        serializer = HeartbeatRequestSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        now = timezone.now()
        updates = {"last_seen_at": now}
        if "app_version" in data:
            updates["app_version"] = data["app_version"]
        if "devices" in data:
            updates["device_status"] = dict(data["devices"])
        Kiosk.objects.filter(pk=self.kiosk.pk).update(**updates)

        store = self.store
        return Response({
            "kiosk": {"id": self.kiosk.id, "name": self.kiosk.name},
            "store": {
                "id": store.id,
                "name": store.name,
                "currency": store.currency,
                "tax_rate": str(store.tax_rate),
                "default_weight_tolerance_g": store.default_weight_tolerance_g,
                "secondary_currency": store.secondary_currency or None,
                "secondary_rate": str(store.secondary_rate) if store.secondary_rate is not None else None,
            },
            "server_time": now.isoformat(),
        })


class StaffVerifyView(KioskViewMixin, APIView):
    @extend_schema(request=StaffVerifySerializer, responses={200: StaffIdentitySerializer},
                   summary="Verify a staff code + PIN for this kiosk's store (403 INVALID_STAFF_PIN otherwise)")
    def post(self, request):
        serializer = StaffVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = verify_staff(self.store, serializer.validated_data["staff_code"], serializer.validated_data["pin"])
        return Response(StaffIdentitySerializer({
            "id": user.id,
            "name": user.get_full_name() or user.email,
            "role": user.role,
            "staff_code": user.staff_code,
        }).data)
