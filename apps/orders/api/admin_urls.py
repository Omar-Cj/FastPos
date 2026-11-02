from rest_framework.routers import DefaultRouter

from .admin_views import AdminKioskEventViewSet, AdminOrderViewSet, AdminWeightVerificationViewSet

router = DefaultRouter()
router.include_root_view = False
router.register("orders", AdminOrderViewSet, basename="admin-order")
router.register("weight-verifications", AdminWeightVerificationViewSet, basename="admin-weight-verification")
router.register("kiosk-events", AdminKioskEventViewSet, basename="admin-kiosk-event")

urlpatterns = router.urls
