from django.urls import path

from . import kiosk_views as v

urlpatterns = [
    path("orders/<uuid:order_id>/payments/", v.PaymentCreateView.as_view(), name="kiosk-payment-create"),
    path("orders/<uuid:order_id>/payments/<uuid:payment_id>/", v.PaymentDetailView.as_view(),
         name="kiosk-payment-detail"),
    path("orders/<uuid:order_id>/payments/<uuid:payment_id>/cancel/", v.PaymentCancelView.as_view(),
         name="kiosk-payment-cancel"),
    path("orders/<uuid:order_id>/receipt/", v.ReceiptView.as_view(), name="kiosk-order-receipt"),
    path("orders/<uuid:order_id>/events/", v.OrderEventView.as_view(), name="kiosk-order-events"),
]
