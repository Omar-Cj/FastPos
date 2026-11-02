"""Read-only admin views over orders, the weight-verification audit log and kiosk events (store-scoped)."""

import django_filters
from django.db.models import Count, Prefetch
from rest_framework import viewsets

from apps.core.mixins import StoreScopedMixin
from apps.orders.models import KioskEvent, Order, WeightVerification

from .admin_serializers import (
    AdminOrderDetailSerializer,
    AdminOrderListSerializer,
    KioskEventAdminSerializer,
    WeightVerificationSerializer,
)


class OrderFilter(django_filters.FilterSet):
    created_after = django_filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="gte")
    created_before = django_filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="lte")

    class Meta:
        model = Order
        fields = ["status", "kiosk"]


class WeightVerificationFilter(django_filters.FilterSet):
    created_after = django_filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="gte")
    created_before = django_filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="lte")

    class Meta:
        model = WeightVerification
        fields = ["result", "outcome", "kiosk", "product"]


class KioskEventFilter(django_filters.FilterSet):
    created_after = django_filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="gte")
    created_before = django_filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="lte")

    class Meta:
        model = KioskEvent
        fields = ["type", "kiosk"]


class AdminOrderViewSet(StoreScopedMixin, viewsets.ReadOnlyModelViewSet):
    queryset = Order.objects.select_related("kiosk").annotate(item_count=Count("items"))
    filterset_class = OrderFilter
    ordering_fields = ["created_at", "total"]
    ordering = ["-created_at"]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.action == "retrieve":
            qs = qs.prefetch_related(
                "items", "payments",
                Prefetch("verifications", queryset=WeightVerification.objects.select_related("product")),
            )
        return qs

    def get_serializer_class(self):
        return AdminOrderDetailSerializer if self.action == "retrieve" else AdminOrderListSerializer


class AdminWeightVerificationViewSet(StoreScopedMixin, viewsets.ReadOnlyModelViewSet):
    queryset = WeightVerification.objects.select_related("product")
    serializer_class = WeightVerificationSerializer
    filterset_class = WeightVerificationFilter
    ordering_fields = ["created_at", "delta_g"]
    ordering = ["-created_at"]
    store_field = "order__store"


class AdminKioskEventViewSet(StoreScopedMixin, viewsets.ReadOnlyModelViewSet):
    queryset = KioskEvent.objects.select_related("kiosk")
    serializer_class = KioskEventAdminSerializer
    filterset_class = KioskEventFilter
    ordering_fields = ["created_at"]
    ordering = ["-created_at"]
