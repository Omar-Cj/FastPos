from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.orders.models import Order, WeightVerification
from apps.payments.models import Payment
from apps.stores.models import Kiosk

SALES = "/api/v1/admin/reports/sales-summary/"
WEIGHT = "/api/v1/admin/reports/weight-incidents/"
TZ = ZoneInfo("Africa/Mogadishu")


def _at(day, hour=12):
    return datetime(2026, 9, day, hour, 0, tzinfo=TZ)


def _paid(kiosk, total, when, number, method="WAAFI", failed_attempt=False):
    order = Order.objects.create(store=kiosk.store, kiosk=kiosk, status=Order.Status.PAID, subtotal=total,
                                 total=total, paid_at=when, receipt_number=number)
    if failed_attempt:
        Payment.objects.create(order=order, method="EDAHAB", amount=total, currency="USD",
                               customer_phone="252610000000", status=Payment.Status.FAILED)
    Payment.objects.create(order=order, method=method, amount=total, currency="USD",
                           customer_phone="252611111111", status=Payment.Status.SUCCESS, completed_at=when)
    return order


@pytest.fixture
def kiosk2(store):
    k = Kiosk(store=store, name="Kiosk 2")
    k.issue_token()
    k.save()
    return k


@pytest.fixture
def sales(kiosk, kiosk2, other_store):
    _paid(kiosk, Decimal("10.00"), _at(1), 1)
    _paid(kiosk, Decimal("5.50"), _at(1, 23), 2, method="EDAHAB", failed_attempt=True)  # 23:00 local
    _paid(kiosk2, Decimal("4.50"), _at(3), 3)
    _paid(kiosk, Decimal("99.00"), _at(20), 4)  # outside range used below
    Order.objects.create(store=kiosk.store, kiosk=kiosk, status=Order.Status.OPEN, total=Decimal("7"))
    Order.objects.create(store=kiosk.store, kiosk=kiosk, status=Order.Status.CANCELLED, total=Decimal("8"))
    other_kiosk = Kiosk(store=other_store, name="Other")
    other_kiosk.issue_token()
    other_kiosk.save()
    _paid(other_kiosk, Decimal("1000.00"), _at(2), 1)


class TestSalesSummary:
    def test_summary(self, admin_client, sales, kiosk, kiosk2):
        r = admin_client.get(SALES, {"from": "2026-09-01", "to": "2026-09-10"})
        assert r.status_code == 200, r.json()
        body = r.json()
        assert body["from"] == "2026-09-01" and body["to"] == "2026-09-10"
        assert body["orders_count"] == 3
        assert body["gross_total"] == "20.00"
        assert body["average_order"] == "6.67"
        assert body["by_day"] == [
            {"date": "2026-09-01", "orders_count": 2, "total": "15.50"},
            {"date": "2026-09-03", "orders_count": 1, "total": "4.50"},
        ]
        assert body["by_method"] == [
            {"method": "EDAHAB", "count": 1, "total": "5.50"},
            {"method": "WAAFI", "count": 2, "total": "14.50"},
        ]
        assert body["by_kiosk"] == [
            {"kiosk_id": kiosk.id, "kiosk_name": "Kiosk 1", "orders_count": 2, "total": "15.50"},
            {"kiosk_id": kiosk2.id, "kiosk_name": "Kiosk 2", "orders_count": 1, "total": "4.50"},
        ]

    def test_to_is_inclusive(self, admin_client, sales):
        assert admin_client.get(SALES, {"from": "2026-09-20", "to": "2026-09-20"}).json()["orders_count"] == 1

    def test_empty_and_default_range(self, admin_client, store):
        body = admin_client.get(SALES).json()
        assert body["orders_count"] == 0
        assert body["gross_total"] == "0.00" and body["average_order"] == "0.00"
        from datetime import date
        span = date.fromisoformat(body["to"]) - date.fromisoformat(body["from"])
        assert span == timedelta(days=29)

    def test_bad_dates(self, admin_client, store):
        assert admin_client.get(SALES, {"from": "yesterday"}).status_code == 400
        r = admin_client.get(SALES, {"from": "2026-09-10", "to": "2026-09-01"})
        assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_scoping(self, other_admin_client, superuser_client, sales, store):
        body = other_admin_client.get(SALES, {"from": "2026-09-01", "to": "2026-09-10"}).json()
        assert body["gross_total"] == "1000.00"
        assert superuser_client.get(SALES).status_code == 400
        params = {"from": "2026-09-01", "to": "2026-09-10", "store": store.id}
        body = superuser_client.get(SALES, params).json()
        assert body["gross_total"] == "20.00"

    def test_permissions(self, staff_client, kiosk_client):
        assert staff_client.get(SALES).status_code == 403
        assert kiosk_client.get(SALES).status_code in (401, 403)


def _verif(order, kiosk, product, delta, outcome, when):
    result = WeightVerification.Result.PASS if outcome == "ADDED" else WeightVerification.Result.FAIL
    v = WeightVerification.objects.create(
        order=order, kiosk=kiosk, product=product, expected_weight_g=product.expected_weight_g,
        tolerance_g=10,
        measured_weight_g=product.expected_weight_g + delta, delta_g=delta, result=result, outcome=outcome,
    )
    WeightVerification.objects.filter(pk=v.pk).update(created_at=when)  # auto_now_add
    return v


class TestWeightIncidents:
    def test_report(self, admin_client, other_admin_client, kiosk, kiosk2, make_product):
        rice = make_product(barcode="1", name="Rice", expected_weight_g=1000)
        perfume = make_product(barcode="2", name="Perfume", expected_weight_g=320)
        o1 = Order.objects.create(store=kiosk.store, kiosk=kiosk)
        o2 = Order.objects.create(store=kiosk.store, kiosk=kiosk2)
        _verif(o1, kiosk, rice, 3, "ADDED", _at(2))
        _verif(o1, kiosk, rice, 2, "ADDED", _at(2))
        _verif(o1, kiosk, perfume, 700, "REJECTED", _at(2))
        _verif(o2, kiosk2, perfume, 600, "OVERRIDDEN", _at(3))
        _verif(o2, kiosk2, rice, -1000, "REJECTED", _at(4))
        _verif(o2, kiosk2, rice, -1000, "REJECTED", _at(25))  # out of range

        body = admin_client.get(WEIGHT, {"from": "2026-09-01", "to": "2026-09-10"}).json()
        assert (body["attempts"], body["passed"], body["failed"], body["overridden"]) == (5, 2, 3, 1)
        assert body["fail_rate"] == 0.6
        assert body["top_products"] == [
            {"product_id": perfume.id, "barcode": "2", "name": "Perfume", "failed_count": 2,
             "avg_delta_g": 650.0},
            {"product_id": rice.id, "barcode": "1", "name": "Rice", "failed_count": 1,
             "avg_delta_g": -1000.0},
        ]
        assert body["by_kiosk"] == [
            {"kiosk_id": kiosk2.id, "kiosk_name": "Kiosk 2", "failed_count": 2},
            {"kiosk_id": kiosk.id, "kiosk_name": "Kiosk 1", "failed_count": 1},
        ]
        other = other_admin_client.get(WEIGHT, {"from": "2026-09-01", "to": "2026-09-10"}).json()
        assert other["attempts"] == 0 and other["fail_rate"] == 0.0 and other["top_products"] == []

    def test_permissions(self, staff_client, superuser_client):
        assert staff_client.get(WEIGHT).status_code == 403
        assert superuser_client.get(WEIGHT).status_code == 400
