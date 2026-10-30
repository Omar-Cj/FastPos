from rest_framework.routers import DefaultRouter

from .admin_views import PaymentViewSet

router = DefaultRouter()
router.register("payments", PaymentViewSet, basename="admin-payment")

urlpatterns = router.urls
