from django.urls import path

from . import kiosk_views as v

urlpatterns = [
    path("orders/", v.OrderCreateView.as_view(), name="kiosk-order-create"),
    path("orders/<uuid:order_id>/", v.OrderDetailView.as_view(), name="kiosk-order-detail"),
    path("orders/<uuid:order_id>/items/", v.OrderItemAddView.as_view(), name="kiosk-order-item-add"),
    path("orders/<uuid:order_id>/items/override/", v.OrderItemOverrideView.as_view(),
         name="kiosk-order-item-override"),
    path("orders/<uuid:order_id>/items/<int:item_id>/", v.OrderItemDeleteView.as_view(),
         name="kiosk-order-item-delete"),
    path("orders/<uuid:order_id>/cancel/", v.OrderCancelView.as_view(), name="kiosk-order-cancel"),
    path("events/", v.KioskEventCreateView.as_view(), name="kiosk-event-create"),
]
