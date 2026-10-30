"""Store dashboards. Every report is scoped to the admin's store (superusers pass `?store=`) and to an
inclusive `from`..`to` date range interpreted in the store's timezone (default: last 30 days)."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db.models import Avg, Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.orders.models import Order, WeightVerification
from apps.payments.models import Payment
from apps.stores.api.scoping import CheckedStoreScopedMixin
from apps.stores.models import Store

DEFAULT_DAYS = 30
CENTS = Decimal("0.01")

RANGE_PARAMS = [
    OpenApiParameter("from", OpenApiTypes.DATE, OpenApiParameter.QUERY, description="Inclusive start date."),
    OpenApiParameter("to", OpenApiTypes.DATE, OpenApiParameter.QUERY, description="Inclusive end date."),
    OpenApiParameter("store", int, OpenApiParameter.QUERY, description="Required for superusers."),
]


def _money(value) -> str:
    return str((value or Decimal("0")).quantize(CENTS))


class _ReportView(CheckedStoreScopedMixin, APIView):
    def get_range(self, tz):
        today = timezone.now().astimezone(tz).date()

        def _parse(name, fallback):
            raw = self.request.query_params.get(name)
            if not raw:
                return fallback
            try:
                return date.fromisoformat(raw)
            except ValueError as exc:
                raise ValidationError({name: ["Use an ISO date, YYYY-MM-DD."]}) from exc

        date_to = _parse("to", today)
        date_from = _parse("from", date_to - timedelta(days=DEFAULT_DAYS - 1))
        if date_from > date_to:
            raise ValidationError({"from": ["Must be on or before `to`."]})
        start = datetime.combine(date_from, time.min, tzinfo=tz)
        end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=tz)
        return date_from, date_to, start, end

    def get_context(self):
        store = Store.objects.get(pk=self.resolve_store_id())
        try:
            tz = ZoneInfo(store.timezone)
        except (KeyError, ValueError):
            tz = timezone.get_current_timezone()
        return store, tz, *self.get_range(tz)


class SalesSummaryView(_ReportView):
    @extend_schema(parameters=RANGE_PARAMS, responses={200: OpenApiTypes.OBJECT},
                   summary="Paid orders: totals, per day, per payment method, per kiosk")
    def get(self, request):
        store, tz, date_from, date_to, start, end = self.get_context()
        orders = Order.objects.filter(
            store=store, status=Order.Status.PAID, paid_at__gte=start, paid_at__lt=end
        )
        agg = orders.aggregate(n=Count("id"), total=Sum("total"))
        count, gross = agg["n"], agg["total"] or Decimal("0")

        by_day = (
            orders.annotate(day=TruncDate("paid_at", tzinfo=tz))
            .values("day").annotate(n=Count("id"), total=Sum("total")).order_by("day")
        )
        by_method = (
            Payment.objects.filter(status=Payment.Status.SUCCESS, order__in=orders)
            .values("method").annotate(n=Count("id"), total=Sum("amount")).order_by("method")
        )
        by_kiosk = (
            orders.values("kiosk_id", "kiosk__name")
            .annotate(n=Count("id"), total=Sum("total")).order_by("-total", "kiosk_id")
        )
        return Response({
            "from": date_from.isoformat(),
            "to": date_to.isoformat(),
            "orders_count": count,
            "gross_total": _money(gross),
            "average_order": _money(gross / count if count else 0),
            "by_day": [
                {"date": row["day"].isoformat(), "orders_count": row["n"], "total": _money(row["total"])}
                for row in by_day
            ],
            "by_method": [
                {"method": row["method"], "count": row["n"], "total": _money(row["total"])}
                for row in by_method
            ],
            "by_kiosk": [
                {"kiosk_id": row["kiosk_id"], "kiosk_name": row["kiosk__name"], "orders_count": row["n"],
                 "total": _money(row["total"])}
                for row in by_kiosk
            ],
        })


class WeightIncidentsView(_ReportView):
    @extend_schema(parameters=RANGE_PARAMS, responses={200: OpenApiTypes.OBJECT},
                   summary="Weigh attempts: pass/fail/override counts, worst products and kiosks")
    def get(self, request):
        store, tz, date_from, date_to, start, end = self.get_context()
        qs = WeightVerification.objects.filter(order__store=store, created_at__gte=start, created_at__lt=end)
        fail = WeightVerification.Result.FAIL
        agg = qs.aggregate(
            attempts=Count("id"),
            passed=Count("id", filter=Q(result=WeightVerification.Result.PASS)),
            failed=Count("id", filter=Q(result=fail)),
            overridden=Count("id", filter=Q(outcome=WeightVerification.Outcome.OVERRIDDEN)),
        )
        failed = qs.filter(result=fail)
        top_products = (
            failed.values("product_id", "product__barcode", "product__name")
            .annotate(n=Count("id"), avg_delta=Avg("delta_g")).order_by("-n", "product_id")[:10]
        )
        by_kiosk = (
            failed.values("kiosk_id", "kiosk__name").annotate(n=Count("id")).order_by("-n", "kiosk_id")
        )
        attempts = agg["attempts"]
        return Response({
            "from": date_from.isoformat(),
            "to": date_to.isoformat(),
            "attempts": attempts,
            "passed": agg["passed"],
            "failed": agg["failed"],
            "overridden": agg["overridden"],
            "fail_rate": round(agg["failed"] / attempts, 4) if attempts else 0.0,
            "top_products": [
                {"product_id": row["product_id"], "barcode": row["product__barcode"],
                 "name": row["product__name"], "failed_count": row["n"],
                 "avg_delta_g": round(float(row["avg_delta"]), 1)}
                for row in top_products
            ],
            "by_kiosk": [
                {"kiosk_id": row["kiosk_id"], "kiosk_name": row["kiosk__name"], "failed_count": row["n"]}
                for row in by_kiosk
            ],
        })
