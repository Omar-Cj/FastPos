from rest_framework.routers import DefaultRouter

from .admin_views import AdminOrderViewSet, AdminWeightVerificationViewSet

router = DefaultRouter()
router.include_root_view = False
router.register("orders", AdminOrderViewSet, basename="admin-order")
router.register("weight-verifications", AdminWeightVerificationViewSet, basename="admin-weight-verification")

urlpatterns = router.urls
