from django.urls import path

from .kiosk_views import HeartbeatView

urlpatterns = [
    path("heartbeat/", HeartbeatView.as_view(), name="kiosk-heartbeat"),
]
