from rest_framework.routers import DefaultRouter

from .admin_views import KioskViewSet, StoreViewSet

router = DefaultRouter()
router.include_root_view = False
router.register("stores", StoreViewSet, basename="admin-store")
router.register("kiosks", KioskViewSet, basename="admin-kiosk")

urlpatterns = router.urls
