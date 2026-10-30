from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.core.mixins import AdminViewMixin
from apps.stores.api.scoping import CheckedStoreScopedMixin

from .admin_serializers import MeSerializer, SetPinSerializer, StaffSerializer


class MeView(AdminViewMixin, APIView):
    @extend_schema(responses={200: MeSerializer}, summary="The logged-in admin")
    def get(self, request):
        return Response(MeSerializer(request.user).data)


class StaffViewSet(CheckedStoreScopedMixin, viewsets.ModelViewSet):
    queryset = User.objects.filter(is_superuser=False).select_related("store").order_by("id")
    serializer_class = StaffSerializer
    filterset_fields = ["role", "is_active"]
    search_fields = ["email", "first_name", "last_name", "staff_code"]
    ordering_fields = ["email", "date_joined"]

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        if getattr(self, "swagger_fake_view", False) or self.request is None:
            return ctx
        if self.action in ("create", "update", "partial_update"):
            ctx["store_id"] = self.resolve_store_id()
        return ctx

    def perform_destroy(self, instance):
        if instance.pk == self.request.user.pk:
            raise ValidationError({"detail": ["You cannot delete your own account."]})
        instance.delete()

    @extend_schema(request=SetPinSerializer, responses={204: None}, summary="Set a staff member's kiosk PIN")
    @action(detail=True, methods=["post"], url_path="set-pin")
    def set_pin(self, request, pk=None):
        user = self.get_object()
        body = SetPinSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        user.set_pin(body.validated_data["pin"])
        user.save(update_fields=["pin_hash"])
        return Response(status=status.HTTP_204_NO_CONTENT)
