"""Device-level kiosk endpoints."""

from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.mixins import KioskViewMixin
from apps.stores.models import Kiosk


class HeartbeatView(KioskViewMixin, APIView):
    @extend_schema(request=None, responses={200: OpenApiTypes.OBJECT},
                   summary="Kiosk heartbeat: marks the device online and returns store settings")
    def post(self, request):
        now = timezone.now()
        Kiosk.objects.filter(pk=self.kiosk.pk).update(last_seen_at=now)
        store = self.store
        return Response({
            "kiosk": {"id": self.kiosk.id, "name": self.kiosk.name},
            "store": {
                "id": store.id,
                "name": store.name,
                "currency": store.currency,
                "tax_rate": str(store.tax_rate),
                "default_weight_tolerance_g": store.default_weight_tolerance_g,
            },
            "server_time": now.isoformat(),
        })
