from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.core.permissions import IsSuperuser
from apps.stores.models import Kiosk, Store

from .admin_serializers import KioskSerializer, KioskTokenSerializer, StoreSerializer
from .scoping import CheckedStoreScopedMixin


class StoreViewSet(viewsets.ModelViewSet):
    """Platform operators manage stores."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsSuperuser]
    queryset = Store.objects.all().order_by("id")
    serializer_class = StoreSerializer
    search_fields = ["name"]
    ordering_fields = ["name", "created_at"]


class KioskViewSet(CheckedStoreScopedMixin, viewsets.ModelViewSet):
    queryset = Kiosk.objects.all().order_by("id")
    serializer_class = KioskSerializer
    filterset_fields = ["is_active"]
    search_fields = ["name", "serial_number"]

    def perform_create(self, serializer):
        holder = Kiosk()
        self._new_token = holder.issue_token()
        serializer.save(
            store_id=self.resolve_store_id(), key_hash=holder.key_hash, key_prefix=holder.key_prefix
        )

    @extend_schema(responses={201: KioskSerializer},
                   description="The response also contains `token` (plain device token, shown only once).")
    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        response.data["token"] = self._new_token
        return response

    @extend_schema(request=None, responses={200: KioskTokenSerializer},
                   summary="Issue a new device token; the old one stops working immediately")
    @action(detail=True, methods=["post"], url_path="rotate-key")
    def rotate_key(self, request, pk=None):
        kiosk = self.get_object()
        token = kiosk.issue_token()
        kiosk.save(update_fields=["key_hash", "key_prefix", "updated_at"])
        return Response(KioskTokenSerializer({"token": token}).data, status=status.HTTP_200_OK)
