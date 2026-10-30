from django.urls import path
from rest_framework.routers import DefaultRouter

from .admin_views import MeView, StaffViewSet

router = DefaultRouter()
router.include_root_view = False
router.register("staff", StaffViewSet, basename="admin-staff")

urlpatterns = [
    path("me/", MeView.as_view(), name="admin-me"),
    *router.urls,
]
