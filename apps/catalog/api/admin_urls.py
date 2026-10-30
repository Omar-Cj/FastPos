from rest_framework.routers import DefaultRouter

from .admin_views import CategoryViewSet, ProductViewSet

router = DefaultRouter()
router.include_root_view = False
router.register("categories", CategoryViewSet, basename="admin-category")
router.register("products", ProductViewSet, basename="admin-product")

urlpatterns = router.urls
