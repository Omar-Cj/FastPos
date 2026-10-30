from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog.models import Product
from apps.core.exceptions import ProductNotFound
from apps.core.mixins import KioskViewMixin

from .kiosk_serializers import KioskProductSerializer


class ProductLookupView(KioskViewMixin, APIView):
    @extend_schema(
        parameters=[OpenApiParameter("barcode", str, OpenApiParameter.QUERY, required=True)],
        responses={200: KioskProductSerializer},
        summary="Look up an active product of this kiosk's store by barcode",
    )
    def get(self, request):
        barcode = (request.query_params.get("barcode") or "").strip()
        if not barcode:
            raise ValidationError({"barcode": ["This query parameter is required."]})
        product = (
            Product.objects.select_related("store", "category")
            .filter(store=self.store, barcode=barcode, is_active=True)
            .first()
        )
        if product is None:
            raise ProductNotFound()
        return Response(KioskProductSerializer(product).data)
