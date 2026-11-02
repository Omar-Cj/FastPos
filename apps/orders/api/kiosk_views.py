"""Kiosk basket endpoints. Every order is looked up inside the calling kiosk's store."""

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.services import verify_staff
from apps.core.exceptions import NotFound
from apps.core.idempotency import HEADER, get_idempotency_key
from apps.core.mixins import KioskViewMixin
from apps.orders import services
from apps.orders.models import Order

from .kiosk_serializers import (
    AddItemSerializer,
    CancelOrderSerializer,
    ItemResultSerializer,
    KioskEventCreateSerializer,
    KioskEventSerializer,
    OrderItemSerializer,
    OrderSerializer,
    OrderTotalsSerializer,
    OverrideItemSerializer,
    StaffCredentialsSerializer,
    TotalsResultSerializer,
)


class _OrderMixin(KioskViewMixin):
    def get_order(self, order_id) -> Order:
        return get_object_or_404(Order.objects.select_related("store"), pk=order_id, store=self.store)


def _item_result(item, order) -> dict:
    return {"item": OrderItemSerializer(item).data, "order": OrderTotalsSerializer.for_order(order)}


def _order_payload(order_id) -> dict:
    return OrderSerializer(Order.objects.prefetch_related("items").get(pk=order_id)).data


class OrderCreateView(_OrderMixin, APIView):
    @extend_schema(request=None, responses={201: OrderSerializer}, summary="Start a new basket")
    def post(self, request):
        order = services.open_order(self.kiosk)
        return Response(_order_payload(order.pk), status=status.HTTP_201_CREATED)


class OrderDetailView(_OrderMixin, APIView):
    @extend_schema(responses={200: OrderSerializer}, summary="Get a basket with items and totals")
    def get(self, request, order_id):
        return Response(_order_payload(self.get_order(order_id).pk))


class OrderItemAddView(_OrderMixin, APIView):
    @extend_schema(
        request=AddItemSerializer,
        responses={201: ItemResultSerializer, 200: ItemResultSerializer},
        parameters=[OpenApiParameter(
            HEADER, str, OpenApiParameter.HEADER, required=False,
            description="Client UUID; a replay returns the existing item with 200.",
        )],
        summary="Scan + weigh one unit (422 WEIGHT_MISMATCH on failed weigh)",
    )
    def post(self, request, order_id):
        order = self.get_order(order_id)
        body = AddItemSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        item = services.add_item(
            order, self.kiosk, body.validated_data["barcode"], body.validated_data.get("measured_weight_g"),
            client_request_id=get_idempotency_key(request),
        )
        code = status.HTTP_200_OK if getattr(item, "replayed", False) else status.HTTP_201_CREATED
        return Response(_item_result(item, order), status=code)


class OrderItemOverrideView(_OrderMixin, APIView):
    @extend_schema(request=OverrideItemSerializer, responses={201: ItemResultSerializer},
                   summary="Staff approve a rejected weigh")
    def post(self, request, order_id):
        order = self.get_order(order_id)
        body = OverrideItemSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        staff = verify_staff(self.store, data["staff_code"], data["pin"])
        item = services.override_item(order, data["verification_id"], staff, data["reason"])
        return Response(_item_result(item, order), status=status.HTTP_201_CREATED)


class OrderItemDeleteView(_OrderMixin, APIView):
    @extend_schema(request=StaffCredentialsSerializer, responses={200: TotalsResultSerializer},
                   summary="Staff remove one item")
    def delete(self, request, order_id, item_id):
        order = self.get_order(order_id)
        body = StaffCredentialsSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        staff = verify_staff(self.store, body.validated_data["staff_code"], body.validated_data["pin"])
        services.remove_item(order, item_id, staff)
        return Response({"order": OrderTotalsSerializer.for_order(order)})


class OrderCancelView(_OrderMixin, APIView):
    @extend_schema(request=CancelOrderSerializer, responses={200: OrderSerializer},
                   summary="Cancel a basket (staff required when it has items)")
    def post(self, request, order_id):
        order = self.get_order(order_id)
        body = CancelOrderSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        staff = None
        if data.get("staff_code") or data.get("pin"):
            staff = verify_staff(self.store, data.get("staff_code"), data.get("pin"))
        services.cancel_order(order, staff_user=staff, reason=data["reason"])
        return Response(_order_payload(order.pk))


class KioskEventCreateView(KioskViewMixin, APIView):
    @extend_schema(request=KioskEventCreateSerializer, responses={201: KioskEventSerializer},
                   summary="Report an operational event (staff call, lock-out, timeout, fault)")
    def post(self, request):
        body = KioskEventCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        order = None
        if data["order"] is not None:
            order = Order.objects.filter(pk=data["order"]).first()
            if order is None:
                raise NotFound("Order not found.", code="ORDER_NOT_FOUND")
        event = services.record_kiosk_event(self.kiosk, data["type"], order=order, detail=data["detail"])
        return Response(KioskEventSerializer(event).data, status=status.HTTP_201_CREATED)
