"""URL layout.

/api/v1/kiosk/   device endpoints, `Authorization: Kiosk <token>`
/api/v1/admin/   React admin panel endpoints, `Authorization: Bearer <jwt>`, role ADMIN
/api/v1/auth/    JWT obtain / refresh
/api/docs/       Swagger UI, /api/schema/ OpenAPI file
"""

from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

kiosk_patterns = [
    path("", include("apps.stores.api.kiosk_urls")),  # heartbeat
    path("", include("apps.catalog.api.kiosk_urls")),  # products/lookup
    path("", include("apps.orders.api.kiosk_urls")),  # orders, items, override, cancel
    path("", include("apps.payments.api.kiosk_urls")),  # payments, receipt, events
]

admin_patterns = [
    path("", include("apps.accounts.api.admin_urls")),  # me, staff
    path("", include("apps.stores.api.admin_urls")),  # stores, kiosks
    path("", include("apps.catalog.api.admin_urls")),  # categories, products
    path("", include("apps.orders.api.admin_urls")),  # orders, weight-verifications
    path("", include("apps.payments.api.admin_urls")),  # payments
    path("reports/", include("apps.reports.api.admin_urls")),
]

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("api/v1/auth/", include("apps.accounts.api.auth_urls")),
    path("api/v1/kiosk/", include(kiosk_patterns)),
    path("api/v1/admin/", include(admin_patterns)),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
]
