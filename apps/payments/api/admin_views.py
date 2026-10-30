import django_filters
from rest_framework import viewsets

from apps.core.mixins import StoreScopedMixin

from ..models import Payment
from .admin_serializers import AdminPaymentSerializer


class PaymentFilter(django_filters.FilterSet):
    initiated_after = django_filters.IsoDateTimeFilter(field_name="initiated_at", lookup_expr="gte")
    initiated_before = django_filters.IsoDateTimeFilter(field_name="initiated_at", lookup_expr="lt")

    class Meta:
        model = Payment
        fields = ["method", "status", "order"]


class PaymentViewSet(StoreScopedMixin, viewsets.ReadOnlyModelViewSet):
    """Every payment attempt of the admin's store, newest first."""

    queryset = Payment.objects.select_related("order").order_by("-initiated_at")
    serializer_class = AdminPaymentSerializer
    store_field = "order__store"
    filterset_class = PaymentFilter
    search_fields = ["provider_reference", "customer_phone"]
    ordering_fields = ["initiated_at", "amount", "completed_at"]
