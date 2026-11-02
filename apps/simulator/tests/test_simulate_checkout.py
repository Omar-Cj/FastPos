"""End-to-end run of the command against a mocked HTTP server."""

import io
import json

import pytest
import responses
from django.core.management import CommandError, call_command

API = "http://srv.test/api/v1/kiosk/"
OID = "6f1c7d2e-0000-4000-8000-000000000001"
RICE = {"id": 1, "barcode": "6291041500213", "name": "Basmati Rice 1kg", "price": "2.80",
        "expected_weight_g": 1020, "tolerance_g": 25, "requires_weight_check": True}


def _server(outcome="SUCCESS", cancel_order_status="OPEN"):
    responses.post(API + "heartbeat/", json={"kiosk": {"id": 1, "name": "Kiosk 1"},
                                              "store": {"id": 1, "name": "Demo Mart", "currency": "USD",
                                                        "secondary_currency": "SLSH",
                                                        "secondary_rate": "8500.0000"}})
    responses.post(API + "events/", json={"id": 1}, status=201)
    responses.post(API + "orders/", json={"id": OID}, status=201)
    responses.get(API + "products/lookup/", json=RICE)

    def add_item(request):
        measured = json.loads(request.body)["measured_weight_g"]
        delta = measured - 1020
        if abs(delta) > 25:
            return 422, {}, json.dumps({"error": {"code": "WEIGHT_MISMATCH", "message": "m", "details": {
                "verification_id": 11, "expected_weight_g": 1020, "measured_weight_g": measured,
                "tolerance_g": 25, "delta_g": delta}}})
        return 201, {}, json.dumps({"item": {"weight_status": "PASSED"}, "order": {"total": "2.80"}})

    responses.add_callback(responses.POST, API + f"orders/{OID}/items/", callback=add_item)
    responses.post(API + f"orders/{OID}/items/override/", json={"item": {}, "order": {"total": "2.80"}},
                   status=201)

    def cancel(request):
        reason = json.loads(request.body).get("reason", "")
        return 200, {}, json.dumps({"id": OID, "status": "CANCELLED", "cancel_reason": reason})

    responses.add_callback(responses.POST, API + f"orders/{OID}/cancel/", callback=cancel)
    responses.post(API + f"orders/{OID}/payments/p1/cancel/", json={
        "id": "p1", "status": "FAILED", "failure_reason": "Abandoned by customer",
        "order_status": cancel_order_status})
    responses.post(API + f"orders/{OID}/payments/", json={"id": "p1", "status": "PENDING"}, status=202)
    responses.get(API + f"orders/{OID}/payments/p1/", json={"id": "p1", "status": outcome})
    responses.get(API + f"orders/{OID}/receipt/", json={
        "receipt_number": 42, "total": "2.80", "subtotal": "2.80", "tax": "0.00",
        "lines": [{"name": "Basmati Rice 1kg", "quantity": 2, "unit_price": "2.80", "line_total": "5.60"}]})
    responses.post(API + f"orders/{OID}/events/", json={})


def _bodies(url):
    return [json.loads(c.request.body) if c.request.body else None
            for c in responses.calls if c.request.url == url]


def _kiosk_events():
    return _bodies(API + "events/")


def _order_events():
    return [b["type"] for b in _bodies(API + f"orders/{OID}/events/")]


def _run(*args):
    out = io.StringIO()
    call_command("simulate_checkout", "--base-url", "http://srv.test", "--token", "t", "--barcodes",
                 "6291041500213", "--seed", "1", "--poll-interval", "0", *args, stdout=out)
    return out.getvalue()


@responses.activate
def test_honest_checkout():
    _server()
    out = _run("--items", "2")
    assert out.count("→ PASS") == 2
    assert "Receipt #42" in out
    assert "Basmati Rice 1kg x2" in out
    assert "[SUMMARY] items_added=2 failed=0 overridden=0 total=2.80 receipt=42 payment=SUCCESS" in out
    assert _order_events() == ["RECEIPT_PRINTED", "EAS_DEACTIVATED"]
    assert [b["type"] for b in _kiosk_events()] == ["APP_STARTED"]


@responses.activate
def test_swap_with_override():
    _server()
    out = _run("--items", "1", "--scenario", "swap", "--override")
    assert "→ FAIL (delta -" in out
    assert "[OVERRIDE] by S1" in out
    assert "overridden=1" in out


@responses.activate
def test_all_failed_without_override_cancels():
    _server()
    out = _run("--items", "2", "--scenario", "empty")
    assert "order cancelled" in out
    assert "payment=CANCELLED" in out


@responses.activate
def test_payment_failed_reported():
    _server(outcome="FAILED")
    out = _run("--items", "1")
    assert "payment=FAILED" in out


@responses.activate
def test_api_error_exits_nonzero():
    responses.post(API + "heartbeat/", json={"error": {"code": "AUTHENTICATION_FAILED", "message": "bad"}},
                   status=401)
    with pytest.raises(CommandError, match="AUTHENTICATION_FAILED"):
        _run()


@responses.activate
def test_payment_timeout():
    _server(outcome="PENDING")
    with pytest.raises(CommandError, match="still PENDING"):
        _run("--items", "1", "--poll-timeout", "0.05")


@responses.activate
def test_heartbeat_reports_version_and_devices():
    _server()
    out = _run("--items", "1")
    assert "(USD, SLSH @ 8500.0000)" in out
    assert _bodies(API + "heartbeat/") == [{
        "app_version": "simulator",
        "devices": {"scanner": "OK", "scale": "OK", "printer": "OK", "eas": "OK"},
    }]
    assert _kiosk_events()[0] == {"type": "APP_STARTED", "order": None, "detail": {"app_version": "simulator"}}


@responses.activate
def test_abandon_after_cancels_pending_payment():
    _server(outcome="PENDING")
    out = _run("--items", "1", "--phone", "252618888888", "--abandon-after", "2")
    assert len(_bodies(API + f"orders/{OID}/payments/p1/")) == 2  # GET polls
    assert len(_bodies(API + f"orders/{OID}/payments/p1/cancel/")) == 1
    assert _kiosk_events()[1] == {"type": "PAYMENT_TIMEOUT", "order": OID, "detail": {"order": OID, "payment": "p1"}}
    assert "[PAY] cancelled → FAILED Abandoned by customer" in out
    assert "[ORDER] status=OPEN" in out
    assert "payment=FAILED" in out
    assert _order_events() == []  # no receipt / EAS events


@responses.activate
def test_abandon_after_zero_polls_cancels_immediately():
    _server(outcome="PENDING")
    _run("--items", "1", "--abandon-after", "0")
    assert not any(c.request.method == "GET" and "/payments/p1/" in c.request.url for c in responses.calls)
    assert len(_bodies(API + f"orders/{OID}/payments/p1/cancel/")) == 1


@responses.activate
def test_abandon_after_fails_if_order_not_reopened():
    _server(outcome="PENDING", cancel_order_status="AWAITING_PAYMENT")
    with pytest.raises(CommandError, match="Expected the order to be OPEN"):
        _run("--items", "1", "--abandon-after", "1")


@responses.activate
def test_abandon_after_payment_already_succeeded():
    _server(outcome="SUCCESS")
    with pytest.raises(CommandError, match="succeeded before it could be abandoned"):
        _run("--items", "1", "--abandon-after", "1")
    assert _bodies(API + f"orders/{OID}/payments/p1/cancel/") == []


@responses.activate
def test_idle_cancel():
    _server()
    out = _run("--items", "2", "--idle-cancel")
    assert _bodies(API + f"orders/{OID}/cancel/") == [{"reason": "IDLE_TIMEOUT"}]
    assert not any(c.request.url == API + f"orders/{OID}/payments/" for c in responses.calls)
    assert _kiosk_events()[1] == {"type": "IDLE_TIMEOUT", "order": OID, "detail": {"order": OID}}
    assert "[ORDER] status=CANCELLED reason=IDLE_TIMEOUT" in out
    assert "payment=CANCELLED" in out


def test_abandon_and_idle_are_exclusive():
    with pytest.raises(CommandError):
        _run("--abandon-after", "1", "--idle-cancel")
