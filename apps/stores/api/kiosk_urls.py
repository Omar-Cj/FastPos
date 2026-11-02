from django.urls import path

from .kiosk_views import HeartbeatView, StaffVerifyView

urlpatterns = [
    path("heartbeat/", HeartbeatView.as_view(), name="kiosk-heartbeat"),
    path("staff/verify/", StaffVerifyView.as_view(), name="kiosk-staff-verify"),
]
