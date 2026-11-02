from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.idempotency import HEADER, get_idempotency_key
from apps.core.mixins import KioskViewMixin
from apps.orders.models import Order

from .. import services
from ..models import Payment
from .kiosk_serializers import (
    InitiatePaymentSerializer,
    OrderEventResponseSerializer,
    OrderEventSerializer,
    PaymentSerializer,
    PaymentStatusSerializer,
    ReceiptSerializer,
)

ERROR = OpenApiResponse(description='Error envelope `{"error": {"code", "message", "details"}}`.')


class _KioskOrderMixin(KioskViewMixin):
    def get_order(self) -> Order:
        return get_object_or_404(Order.objects.select_related("store", "kiosk"),
                                 pk=self.kwargs["order_id"], store=self.store)


class PaymentCreateView(_KioskOrderMixin, APIView):
    @extend_schema(
        request=InitiatePaymentSerializer,
        parameters=[OpenApiParameter(
            HEADER, str, OpenApiParameter.HEADER, required=False,
            description="UUID; a retry with the same key returns the same payment.",
        )],
        responses={202: PaymentSerializer, 200: PaymentSerializer, 400: ERROR, 404: ERROR, 409: ERROR},
        summary="Start a mobile-money payment (USSD prompt); poll the payment for the result.",
    )
    def post(self, request, order_id):
        order = self.get_order()
        data = InitiatePaymentSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        payment, created = services.initiate_payment(
            order, data.validated_data["method"], data.validated_data["customer_phone"],
            client_request_id=get_idempotency_key(request),
        )
        payment.refresh_from_db()  # eager Celery may already have settled it
        return Response(PaymentSerializer(payment).data,
                        status=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK)


class PaymentDetailView(_KioskOrderMixin, APIView):
    @extend_schema(responses={200: PaymentStatusSerializer, 404: ERROR}, summary="Poll a payment attempt.")
    def get(self, request, order_id, payment_id):
        order = self.get_order()
        payment = get_object_or_404(Payment.objects.select_related("order"), pk=payment_id, order=order)
        return Response(PaymentStatusSerializer(payment).data)


class PaymentCancelView(_KioskOrderMixin, APIView):
    @extend_schema(
        request=None,
        responses={200: PaymentStatusSerializer, 404: ERROR, 409: ERROR},
        summary="Abandon a PENDING payment (customer walked away / kiosk countdown expired).",
        description="Marks the payment FAILED with reason \"Abandoned by customer\" and reopens the order. "
                    "Idempotent: an already FAILED payment is returned unchanged. "
                    "409 PAYMENT_NOT_PENDING if the payment already succeeded.",
    )
    def post(self, request, order_id, payment_id):
        order = self.get_order()
        payment = get_object_or_404(Payment, pk=payment_id, order=order)
        payment = services.abandon_payment(payment)
        return Response(PaymentStatusSerializer(payment).data)


class ReceiptView(_KioskOrderMixin, APIView):
    @extend_schema(responses={200: ReceiptSerializer, 404: ERROR, 409: ERROR},
                   summary="Receipt of a paid order.")
    def get(self, request, order_id):
        return Response(services.build_receipt(self.get_order()))


class OrderEventView(_KioskOrderMixin, APIView):
    @extend_schema(request=OrderEventSerializer, responses={200: OrderEventResponseSerializer, 409: ERROR},
                   summary="Record a post-payment hardware event (receipt printed, EAS tags deactivated).")
    def post(self, request, order_id):
        order = self.get_order()
        data = OrderEventSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        event_type = data.validated_data["type"]
        recorded_at = services.record_event(order, event_type)
        return Response(OrderEventResponseSerializer(
            {"order_id": order.id, "type": event_type, "recorded_at": recorded_at}).data)
