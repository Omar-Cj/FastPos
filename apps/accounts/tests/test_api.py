from apps.accounts.models import User

STAFF = "/api/v1/admin/staff/"


class TestMe:
    def test_me_admin(self, admin_client, admin_user, store):
        r = admin_client.get("/api/v1/admin/me/")
        assert r.status_code == 200
        assert r.json() == {
            "id": admin_user.id, "email": admin_user.email, "role": "ADMIN",
            "store": {"id": store.id, "name": store.name}, "is_superuser": False,
        }

    def test_me_superuser(self, superuser_client):
        body = superuser_client.get("/api/v1/admin/me/").json()
        assert body["store"] is None
        assert body["is_superuser"] is True

    def test_me_rejects_staff_and_kiosk(self, staff_client, kiosk_client):
        assert staff_client.get("/api/v1/admin/me/").status_code == 403
        assert kiosk_client.get("/api/v1/admin/me/").status_code in (401, 403)


class TestStaff:
    def test_list_excludes_superusers_and_other_stores(
        self, admin_client, admin_user, staff_user, superuser_client, other_admin_client
    ):
        emails = {u["email"] for u in admin_client.get(STAFF).json()["results"]}
        assert emails == {admin_user.email, staff_user.email}

    def test_never_exposes_secrets(self, admin_client, staff_user):
        body = admin_client.get(f"{STAFF}{staff_user.id}/").json()
        assert body["has_pin"] is True
        assert "pin_hash" not in body and "password" not in body and "pin" not in body

    def test_create_with_pin(self, admin_client, store):
        r = admin_client.post(STAFF, {
            "email": "new@demo.test", "password": "longpass1", "pin": "4321", "staff_code": "S2",
            "first_name": "Amina",
        })
        assert r.status_code == 201, r.json()
        assert r.json()["role"] == "STAFF"
        assert r.json()["has_pin"] is True
        user = User.objects.get(email="new@demo.test")
        assert user.store_id == store.id
        assert user.check_pin("4321")
        assert user.check_password("longpass1")

    def test_create_requires_password(self, admin_client):
        r = admin_client.post(STAFF, {"email": "x@demo.test"})
        assert r.status_code == 400
        assert "password" in r.json()["error"]["details"]

    def test_invalid_pin(self, admin_client):
        r = admin_client.post(STAFF, {"email": "x@demo.test", "password": "longpass1", "pin": "12a"})
        assert r.status_code == 400
        assert "pin" in r.json()["error"]["details"]

    def test_duplicate_staff_code(self, admin_client, staff_user):
        r = admin_client.post(STAFF, {"email": "x@demo.test", "password": "longpass1", "staff_code": "S1"})
        assert r.status_code == 400
        assert "staff_code" in r.json()["error"]["details"]

    def test_set_pin_action(self, admin_client, staff_user):
        r = admin_client.post(f"{STAFF}{staff_user.id}/set-pin/", {"pin": "567890"})
        assert r.status_code == 204
        staff_user.refresh_from_db()
        assert staff_user.check_pin("567890")
        assert admin_client.post(f"{STAFF}{staff_user.id}/set-pin/", {"pin": "12"}).status_code == 400

    def test_update_and_delete(self, admin_client, staff_user):
        r = admin_client.patch(f"{STAFF}{staff_user.id}/", {"is_active": False, "last_name": "Ali"})
        assert r.status_code == 200
        assert r.json()["is_active"] is False
        assert admin_client.delete(f"{STAFF}{staff_user.id}/").status_code == 204
        assert not User.objects.filter(pk=staff_user.id).exists()

    def test_cannot_delete_self(self, admin_client, admin_user):
        r = admin_client.delete(f"{STAFF}{admin_user.id}/")
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_store_scoping(self, other_admin_client, staff_user):
        assert other_admin_client.get(f"{STAFF}{staff_user.id}/").status_code == 404
        assert other_admin_client.post(f"{STAFF}{staff_user.id}/set-pin/", {"pin": "0000"}).status_code == 404
        assert other_admin_client.delete(f"{STAFF}{staff_user.id}/").status_code == 404

    def test_superuser_needs_store(self, superuser_client, staff_user, store):
        assert superuser_client.get(STAFF).status_code == 400
        assert superuser_client.get(f"{STAFF}?store={store.id}").json()["count"] == 1

    def test_staff_role_forbidden(self, staff_client):
        assert staff_client.get(STAFF).status_code == 403
