from django.urls import path

from .kiosk_views import ProductLookupView

urlpatterns = [
    path("products/lookup/", ProductLookupView.as_view(), name="kiosk-product-lookup"),
]
