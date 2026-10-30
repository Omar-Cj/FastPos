from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APIClient

from apps.stores.models import Kiosk, Store

KIOSKS = "/api/v1/admin/kiosks/"
STORES = "/api/v1/admin/stores/"


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
        kiosk.refresh_from_db()
        assert kiosk.last_seen_at is not None

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
