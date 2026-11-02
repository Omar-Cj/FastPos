import json
import uuid

import pytest
import responses

from apps.simulator.kiosk_client import KioskAPIError, KioskClient

BASE = "http://kiosk.test"
API = BASE + "/api/v1/kiosk/"
OID = "6f1c7d2e-0000-4000-8000-000000000001"


@pytest.fixture
def client():
    return KioskClient(BASE + "/", "tok123")


@responses.activate
def test_heartbeat_sends_token(client):
    responses.post(API + "heartbeat/", json={"kiosk": {"id": 1}}, status=200)
    assert client.heartbeat() == (200, {"kiosk": {"id": 1}})
    assert responses.calls[0].request.headers["Authorization"] == "Kiosk tok123"


@responses.activate
def test_lookup_query(client):
    responses.get(API + "products/lookup/", json={"id": 5}, status=200)
    client.lookup("62 91")
    assert responses.calls[0].request.url == API + "products/lookup/?barcode=62%2091"


@responses.activate
def test_add_item_idempotency_key_and_body(client):
    responses.post(API + f"orders/{OID}/items/", json={"item": {}, "order": {}}, status=201)
    client.add_item(OID, "123", 505)
    client.add_item(OID, "123", 505)
    keys = [c.request.headers["Idempotency-Key"] for c in responses.calls]
    assert len({str(uuid.UUID(k)) for k in keys}) == 2
    assert json.loads(responses.calls[0].request.body) == {"barcode": "123", "measured_weight_g": 505}


@responses.activate
def test_weight_mismatch_returns_normally(client):
    details = {"verification_id": 9, "delta_g": 300}
    err = {"error": {"code": "WEIGHT_MISMATCH", "message": "m", "details": details}}
    responses.post(API + f"orders/{OID}/items/", json=err, status=422)
    status, body = client.add_item(OID, "123", 820)
    assert status == 422
    assert body["error"]["details"]["verification_id"] == 9


@responses.activate
def test_other_422_raises(client):
    responses.post(API + f"orders/{OID}/payments/",
                   json={"error": {"code": "UNPROCESSABLE", "message": "no"}}, status=422)
    with pytest.raises(KioskAPIError) as exc:
        client.pay(OID, "WAAFI", "252611111111")
    assert exc.value.status == 422


@responses.activate
def test_conflict_raises(client):
    responses.post(API + f"orders/{OID}/items/",
                   json={"error": {"code": "ORDER_NOT_OPEN", "message": "closed", "details": {"s": 1}}},
                   status=409)
    with pytest.raises(KioskAPIError) as exc:
        client.add_item(OID, "1", 10)
    assert (exc.value.code, exc.value.message, exc.value.details, exc.value.status) == (
        "ORDER_NOT_OPEN", "closed", {"s": 1}, 409)


@responses.activate
def test_non_json_error(client):
    responses.get(API + f"orders/{OID}/receipt/", body="<html>boom</html>", status=500)
    with pytest.raises(KioskAPIError) as exc:
        client.receipt(OID)
    assert exc.value.code == "HTTP_500"


@responses.activate
def test_paths_and_bodies(client):
    responses.post(API + f"orders/{OID}/items/override/", json={}, status=201)
    responses.delete(API + f"orders/{OID}/items/7/", json={}, status=200)
    responses.post(API + f"orders/{OID}/cancel/", json={}, status=200)
    responses.post(API + f"orders/{OID}/payments/", json={"id": "p1", "status": "PENDING"}, status=202)
    responses.get(API + f"orders/{OID}/payments/p1/", json={"id": "p1", "status": "SUCCESS"}, status=200)
    responses.post(API + f"orders/{OID}/events/", json={}, status=200)
    responses.post(API + "orders/", json={"id": OID}, status=201)

    client.open_order()
    client.override(OID, 3, "S1", "1234", "ok")
    client.remove_item(OID, 7, "S1", "1234")
    client.cancel(OID, reason="bye")
    assert client.pay(OID, "EDAHAB", "252621111111") == (202, {"id": "p1", "status": "PENDING"})
    assert client.payment_status(OID, "p1")[1]["status"] == "SUCCESS"
    client.event(OID, "RECEIPT_PRINTED")

    bodies = [json.loads(c.request.body) if c.request.body else None for c in responses.calls]
    assert bodies[1] == {"verification_id": 3, "staff_code": "S1", "pin": "1234", "reason": "ok"}
    assert bodies[2] == {"staff_code": "S1", "pin": "1234"}
    assert bodies[3] == {"reason": "bye"}
    assert bodies[4] == {"method": "EDAHAB", "customer_phone": "252621111111"}
    assert "Idempotency-Key" in responses.calls[4].request.headers
    assert bodies[6] == {"type": "RECEIPT_PRINTED"}


@responses.activate
def test_heartbeat_without_args_sends_no_body(client):
    responses.post(API + "heartbeat/", json={}, status=200)
    client.heartbeat()
    assert responses.calls[0].request.body is None


@responses.activate
def test_heartbeat_with_status_body(client):
    responses.post(API + "heartbeat/", json={}, status=200)
    devices = {"scanner": "OK", "scale": "WARN", "printer": "OK", "eas": "OFF"}
    client.heartbeat(app_version="0.1.0", devices=devices)
    client.heartbeat(app_version="0.2.0")
    assert json.loads(responses.calls[0].request.body) == {"app_version": "0.1.0", "devices": devices}
    assert json.loads(responses.calls[1].request.body) == {"app_version": "0.2.0"}


@responses.activate
def test_verify_staff(client):
    identity = {"id": 2, "name": "Hodan Cabdi", "role": "STAFF", "staff_code": "1001"}
    responses.post(API + "staff/verify/", json=identity, status=200)
    assert client.verify_staff("1001", "1234") == (200, identity)
    assert json.loads(responses.calls[0].request.body) == {"staff_code": "1001", "pin": "1234"}


@responses.activate
def test_verify_staff_bad_pin_raises(client):
    responses.post(API + "staff/verify/", json={"error": {"code": "INVALID_STAFF_PIN", "message": "no"}},
                   status=403)
    with pytest.raises(KioskAPIError) as exc:
        client.verify_staff("1001", "0000")
    assert (exc.value.code, exc.value.status) == ("INVALID_STAFF_PIN", 403)


@responses.activate
def test_kiosk_event_bodies(client):
    responses.post(API + "events/", json={"id": 1}, status=201)
    assert client.kiosk_event("APP_STARTED") == (201, {"id": 1})
    client.kiosk_event("PAYMENT_TIMEOUT", order_id=uuid.UUID(OID), detail={"order": OID, "payment": "p1"})
    bodies = [json.loads(c.request.body) for c in responses.calls]
    assert bodies[0] == {"type": "APP_STARTED", "order": None}
    assert bodies[1] == {"type": "PAYMENT_TIMEOUT", "order": OID, "detail": {"order": OID, "payment": "p1"}}


@responses.activate
def test_cancel_payment(client):
    body = {"id": "p1", "status": "FAILED", "failure_reason": "Abandoned by customer", "order_status": "OPEN"}
    responses.post(API + f"orders/{OID}/payments/p1/cancel/", json=body, status=200)
    assert client.cancel_payment(OID, "p1") == (200, body)


@responses.activate
def test_cancel_payment_conflict_raises(client):
    responses.post(API + f"orders/{OID}/payments/p1/cancel/",
                   json={"error": {"code": "PAYMENT_NOT_PENDING", "message": "paid"}}, status=409)
    with pytest.raises(KioskAPIError) as exc:
        client.cancel_payment(OID, "p1")
    assert exc.value.code == "PAYMENT_NOT_PENDING"
