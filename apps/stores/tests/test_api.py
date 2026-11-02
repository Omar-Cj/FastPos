from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.stores.models import Kiosk, Store

KIOSKS = "/api/v1/admin/kiosks/"
STORES = "/api/v1/admin/stores/"
HEARTBEAT = "/api/v1/kiosk/heartbeat/"
STAFF_VERIFY = "/api/v1/kiosk/staff/verify/"


class TestHeartbeat:
    def test_heartbeat_marks_seen_and_returns_store(self, kiosk_client, kiosk, store):
        r = kiosk_client.post("/api/v1/kiosk/heartbeat/")
        assert r.status_code == 200
        body = r.json()
        assert body["kiosk"] == {"id": kiosk.id, "name": "Kiosk 1"}
        assert body["store"]["id"] == store.id
        assert body["store"]["currency"] == "USD"
        assert body["store"]["default_weight_tolerance_g"] == 10
        assert "server_time" in body
        assert set(body["store"]) == {
            "id", "name", "currency", "tax_rate", "default_weight_tolerance_g", "secondary_currency", "secondary_rate",
        }
        assert body["store"]["secondary_currency"] is None
        assert body["store"]["secondary_rate"] is None
        kiosk.refresh_from_db()
        assert kiosk.last_seen_at is not None
        assert kiosk.app_version == ""
        assert kiosk.device_status == {}

    def test_heartbeat_returns_secondary_currency(self, kiosk_client, store):
        Store.objects.filter(pk=store.pk).update(secondary_currency="SOS", secondary_rate="8500")
        body = kiosk_client.post(HEARTBEAT).json()
        assert body["store"]["secondary_currency"] == "SOS"
        assert body["store"]["secondary_rate"] == "8500.0000"

    def test_heartbeat_body_stored_and_visible_to_admin(self, kiosk_client, admin_client, kiosk):
        devices = {"scanner": "OK", "scale": "WARN", "printer": "FAULT", "eas": "OFF"}
        r = kiosk_client.post(HEARTBEAT, {"app_version": "0.1.0", "devices": devices}, format="json")
        assert r.status_code == 200, r.json()
        kiosk.refresh_from_db()
        assert kiosk.app_version == "0.1.0"
        assert kiosk.device_status == devices
        detail = admin_client.get(f"{KIOSKS}{kiosk.id}/").json()
        assert detail["app_version"] == "0.1.0"
        assert detail["device_status"] == devices
        assert detail["is_online"] is True

    def test_heartbeat_partial_body_keeps_other_fields(self, kiosk_client, kiosk):
        kiosk_client.post(HEARTBEAT, {"app_version": "0.1.0", "devices": {"scanner": "OK"}}, format="json")
        r = kiosk_client.post(HEARTBEAT, {"devices": {"scale": "FAULT"}}, format="json")
        assert r.status_code == 200
        kiosk.refresh_from_db()
        assert kiosk.app_version == "0.1.0"
        assert kiosk.device_status == {"scale": "FAULT"}

    def test_heartbeat_invalid_device_value(self, kiosk_client, kiosk):
        r = kiosk_client.post(HEARTBEAT, {"devices": {"scanner": "BROKEN"}}, format="json")
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "VALIDATION_ERROR"
        kiosk.refresh_from_db()
        assert kiosk.device_status == {}
        assert kiosk.last_seen_at is None

    def test_heartbeat_unknown_device_key(self, kiosk_client):
        r = kiosk_client.post(HEARTBEAT, {"devices": {"scanner": "OK", "camera": "OK"}}, format="json")
        assert r.status_code == 400
        error = r.json()["error"]
        assert error["code"] == "VALIDATION_ERROR"
        assert "camera" in error["details"]["devices"]

    def test_heartbeat_app_version_too_long(self, kiosk_client):
        r = kiosk_client.post(HEARTBEAT, {"app_version": "x" * 33}, format="json")
        assert r.status_code == 400

    def test_heartbeat_requires_kiosk_token(self, admin_client):
        assert admin_client.post("/api/v1/kiosk/heartbeat/").status_code in (401, 403)


class TestStores:
    def test_superuser_crud(self, superuser_client):
        r = superuser_client.post(STORES, {"name": "New Mart", "tax_rate": "0.05"})
        assert r.status_code == 201, r.json()
        assert r.json()["next_receipt_number"] == 1
        sid = r.json()["id"]
        r = superuser_client.patch(f"{STORES}{sid}/", {"next_receipt_number": 99, "name": "Renamed"})
        assert r.status_code == 200
        assert Store.objects.get(pk=sid).next_receipt_number == 1
        assert superuser_client.get(STORES).json()["count"] == 1

    def test_store_admin_forbidden(self, admin_client):
        assert admin_client.get(STORES).status_code == 403

    def test_invalid_timezone(self, superuser_client):
        r = superuser_client.post(STORES, {"name": "X", "timezone": "Mars/Base"})
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_secondary_currency_round_trip(self, superuser_client):
        r = superuser_client.post(STORES, {"name": "Sh Mart", "secondary_currency": "slsh", "secondary_rate": "8500"})
        assert r.status_code == 201, r.json()
        assert r.json()["secondary_currency"] == "SLSH"
        assert r.json()["secondary_rate"] == "8500.0000"
        sid = r.json()["id"]
        detail = superuser_client.get(f"{STORES}{sid}/").json()
        assert detail["secondary_currency"] == "SLSH"
        assert detail["secondary_rate"] == "8500.0000"
        # Patching only the rate keeps the code.
        r = superuser_client.patch(f"{STORES}{sid}/", {"secondary_rate": "9000.5"}, format="json")
        assert r.status_code == 200, r.json()
        assert r.json()["secondary_currency"] == "SLSH"
        assert r.json()["secondary_rate"] == "9000.5000"
        # Clearing the code clears the rate.
        r = superuser_client.patch(f"{STORES}{sid}/", {"secondary_currency": None}, format="json")
        assert r.status_code == 200, r.json()
        assert r.json()["secondary_currency"] is None
        assert r.json()["secondary_rate"] is None
        store = Store.objects.get(pk=sid)
        assert store.secondary_currency is None
        assert store.secondary_rate is None

    def test_store_without_secondary_currency(self, superuser_client):
        r = superuser_client.post(STORES, {"name": "Plain"})
        assert r.status_code == 201
        assert r.json()["secondary_currency"] is None
        assert r.json()["secondary_rate"] is None

    def test_secondary_currency_validation(self, superuser_client, store):
        def err(payload):
            r = superuser_client.patch(f"{STORES}{store.id}/", payload, format="json")
            assert r.status_code == 400, r.json()
            assert r.json()["error"]["code"] == "VALIDATION_ERROR"
            return r.json()["error"]["details"]

        assert "secondary_rate" in err({"secondary_currency": "SOS"})
        assert "secondary_rate" in err({"secondary_currency": "SOS", "secondary_rate": "0"})
        assert "secondary_rate" in err({"secondary_currency": "SOS", "secondary_rate": "-1"})
        assert "secondary_currency" in err({"secondary_currency": "SO", "secondary_rate": "8500"})
        assert "secondary_currency" in err({"secondary_currency": "S0S", "secondary_rate": "8500"})
        assert "secondary_currency" in err({"secondary_currency": "SLSHX", "secondary_rate": "8500"})
        store.refresh_from_db()
        assert store.secondary_currency is None
        r = superuser_client.patch(f"{STORES}{store.id}/", {"secondary_currency": "sos", "secondary_rate": "8500"},
                                   format="json")
        assert r.status_code == 200, r.json()
        assert r.json()["secondary_currency"] == "SOS"


class TestStaffVerify:
    def test_valid_credentials(self, kiosk_client, staff_user):
        staff_user.first_name, staff_user.last_name = "Amina", "Ali"
        staff_user.save()
        r = kiosk_client.post(STAFF_VERIFY, {"staff_code": "S1", "pin": "1234"}, format="json")
        assert r.status_code == 200, r.json()
        assert r.json() == {"id": staff_user.id, "name": "Amina Ali", "role": "STAFF", "staff_code": "S1"}

    def test_name_falls_back_to_email(self, kiosk_client, staff_user):
        r = kiosk_client.post(STAFF_VERIFY, {"staff_code": "S1", "pin": "1234"}, format="json")
        assert r.status_code == 200
        assert r.json()["name"] == "staff@demo.test"

    def test_wrong_pin(self, kiosk_client, staff_user):
        r = kiosk_client.post(STAFF_VERIFY, {"staff_code": "S1", "pin": "0000"}, format="json")
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "INVALID_STAFF_PIN"

    def test_unknown_code(self, kiosk_client, staff_user):
        r = kiosk_client.post(STAFF_VERIFY, {"staff_code": "NOPE", "pin": "1234"}, format="json")
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "INVALID_STAFF_PIN"

    def test_other_store_staff_rejected(self, kiosk_client, other_store):
        user = User.objects.create_user(
            email="staff@other.test", password="pass1234", role=User.Role.STAFF, store=other_store, staff_code="S9"
        )
        user.set_pin("1234")
        user.save()
        r = kiosk_client.post(STAFF_VERIFY, {"staff_code": "S9", "pin": "1234"}, format="json")
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "INVALID_STAFF_PIN"

    def test_missing_fields(self, kiosk_client):
        r = kiosk_client.post(STAFF_VERIFY, {"staff_code": "S1"}, format="json")
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_jwt_rejected(self, staff_client, admin_client, staff_user):
        for client in (staff_client, admin_client):
            r = client.post(STAFF_VERIFY, {"staff_code": "S1", "pin": "1234"}, format="json")
            assert r.status_code in (401, 403)
            assert r.json()["error"]["code"] in ("AUTHENTICATION_FAILED", "PERMISSION_DENIED")


class TestKiosks:
    def test_create_returns_token_once(self, admin_client, store):
        r = admin_client.post(KIOSKS, {"name": "Kiosk 2", "serial_number": "SN-2"})
        assert r.status_code == 201, r.json()
        body = r.json()
        token = body["token"]
        assert body["store"] == store.id
        assert body["key_prefix"] == token[:8]
        assert Kiosk.authenticate_token(token).id == body["id"]
        detail = admin_client.get(f"{KIOSKS}{body['id']}/").json()
        assert "token" not in detail
        assert "key_hash" not in detail

    def test_new_kiosk_token_works(self, admin_client):
        token = admin_client.post(KIOSKS, {"name": "K"}).json()["token"]
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f"Kiosk {token}")
        assert c.post("/api/v1/kiosk/heartbeat/").status_code == 200

    def test_rotate_key_invalidates_old(self, admin_client, kiosk):
        old = kiosk.plain_token
        r = admin_client.post(f"{KIOSKS}{kiosk.id}/rotate-key/")
        assert r.status_code == 200
        new = r.json()["token"]
        assert new != old
        assert Kiosk.authenticate_token(old) is None
        assert Kiosk.authenticate_token(new).id == kiosk.id

    def test_is_online(self, admin_client, kiosk):
        assert admin_client.get(f"{KIOSKS}{kiosk.id}/").json()["is_online"] is False
        Kiosk.objects.filter(pk=kiosk.pk).update(last_seen_at=timezone.now() - timedelta(seconds=30))
        assert admin_client.get(f"{KIOSKS}{kiosk.id}/").json()["is_online"] is True
        Kiosk.objects.filter(pk=kiosk.pk).update(last_seen_at=timezone.now() - timedelta(minutes=5))
        assert admin_client.get(f"{KIOSKS}{kiosk.id}/").json()["is_online"] is False

    def test_other_store_admin_cannot_see_or_modify(self, other_admin_client, kiosk):
        assert other_admin_client.get(KIOSKS).json()["count"] == 0
        assert other_admin_client.get(f"{KIOSKS}{kiosk.id}/").status_code == 404
        assert other_admin_client.patch(f"{KIOSKS}{kiosk.id}/", {"name": "x"}).status_code == 404
        assert other_admin_client.post(f"{KIOSKS}{kiosk.id}/rotate-key/").status_code == 404
        assert other_admin_client.delete(f"{KIOSKS}{kiosk.id}/").status_code == 404

    def test_superuser_needs_store_param(self, superuser_client, kiosk, store):
        r = superuser_client.get(KIOSKS)
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "VALIDATION_ERROR"
        assert superuser_client.get(f"{KIOSKS}?store={store.id}").json()["count"] == 1
        assert superuser_client.get(f"{KIOSKS}?store=99999").status_code == 400
        r = superuser_client.post(f"{KIOSKS}?store={store.id}", {"name": "Root kiosk"})
        assert r.status_code == 201
        assert r.json()["store"] == store.id

    def test_staff_and_kiosk_rejected(self, staff_client, kiosk_client):
        assert staff_client.get(KIOSKS).status_code == 403
        assert kiosk_client.get(KIOSKS).status_code in (401, 403)
