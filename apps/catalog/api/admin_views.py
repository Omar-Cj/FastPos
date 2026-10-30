from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from apps.catalog import services
from apps.catalog.models import Category, Product
from apps.stores.api.scoping import CheckedStoreScopedMixin
from apps.stores.models import Store

from .admin_serializers import (
    CategorySerializer,
    ImportRequestSerializer,
    ImportResultSerializer,
    ProductSerializer,
)


class _StoreContextMixin(CheckedStoreScopedMixin):
    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        if self.request is not None and self.action in ("create", "update", "partial_update"):
            ctx["store_id"] = self.resolve_store_id()
        return ctx


class CategoryViewSet(_StoreContextMixin, viewsets.ModelViewSet):
    queryset = Category.objects.all().order_by("name")
    serializer_class = CategorySerializer
    search_fields = ["name"]
    ordering_fields = ["name", "created_at"]


class ProductViewSet(_StoreContextMixin, viewsets.ModelViewSet):
    queryset = Product.objects.select_related("store", "category").order_by("name")
    serializer_class = ProductSerializer
    filterset_fields = ["category", "is_active", "requires_weight_check"]
    search_fields = ["barcode", "name"]
    ordering_fields = ["name", "price", "updated_at"]

    @extend_schema(request={"multipart/form-data": ImportRequestSerializer},
                   responses={200: ImportResultSerializer},
                   summary="Upsert products by barcode from a CSV file")
    @action(detail=False, methods=["post"], url_path="import", parser_classes=[MultiPartParser, FormParser])
    def import_csv(self, request):
        upload = request.FILES.get("file")
        if upload is None:
            raise ValidationError({"file": ["A CSV file is required."]})
        try:
            result = services.import_products_csv(Store.objects.get(pk=self.resolve_store_id()), upload)
        except services.CSVFormatError as exc:
            raise ValidationError({"file": [str(exc)]}) from exc
        return Response(ImportResultSerializer(result).data)

    @extend_schema(responses={(200, "text/csv"): OpenApiResponse(OpenApiTypes.BINARY)},
                   summary="Download products as CSV (same layout as the import)")
    @action(detail=False, methods=["get"], url_path="export", pagination_class=None)
    def export_csv(self, request):
        body = services.export_products_csv(self.filter_queryset(self.get_queryset()))
        stamp = timezone.localdate().isoformat()
        response = HttpResponse(body, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="products-{stamp}.csv"'
        return response
