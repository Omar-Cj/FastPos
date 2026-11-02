import io

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.catalog.models import Category, Product
from apps.catalog.services import import_products_csv

PRODUCTS = "/api/v1/admin/products/"
CATEGORIES = "/api/v1/admin/categories/"
LOOKUP = "/api/v1/kiosk/products/lookup/"


class TestKioskLookup:
    def test_lookup(self, kiosk_client, product, category):
        r = kiosk_client.get(LOOKUP, {"barcode": product.barcode})
        assert r.status_code == 200
        assert r.json() == {
            "id": product.id, "barcode": product.barcode, "name": "Rice 500g", "name_so": None, "price": "2.50",
            "expected_weight_g": 500, "tolerance_g": 10, "requires_weight_check": True, "has_eas_tag": True,
            "category": "Grocery", "image_url": None,
        }

    def test_lookup_name_so(self, kiosk_client, make_product):
        make_product(barcode="77", name_so="Bariis")
        assert kiosk_client.get(LOOKUP, {"barcode": "77"}).json()["name_so"] == "Bariis"

    def test_effective_tolerance_from_product(self, kiosk_client, make_product):
        make_product(barcode="42", tolerance=25)
        assert kiosk_client.get(LOOKUP, {"barcode": "42"}).json()["tolerance_g"] == 25

    def test_missing_inactive_or_other_store(self, kiosk_client, make_product, other_store):
        make_product(barcode="1", is_active=False)
        make_product(barcode="2", store_=other_store)
        for code in ("1", "2", "nope"):
            r = kiosk_client.get(LOOKUP, {"barcode": code})
            assert r.status_code == 404
            assert r.json()["error"]["code"] == "PRODUCT_NOT_FOUND"

    def test_barcode_required(self, kiosk_client):
        r = kiosk_client.get(LOOKUP)
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_admin_jwt_rejected(self, admin_client, product):
        assert admin_client.get(LOOKUP, {"barcode": product.barcode}).status_code in (401, 403)


class TestCategories:
    def test_crud_and_scoping(self, admin_client, other_admin_client, category):
        r = admin_client.post(CATEGORIES, {"name": "Dairy"})
        assert r.status_code == 201
        assert admin_client.post(CATEGORIES, {"name": "dairy"}).status_code == 400
        assert admin_client.get(CATEGORIES).json()["count"] == 2
        assert other_admin_client.get(CATEGORIES).json()["count"] == 0
        assert other_admin_client.patch(f"{CATEGORIES}{category.id}/", {"name": "x"}).status_code == 404
        # same name in another store is fine
        assert other_admin_client.post(CATEGORIES, {"name": "Grocery"}).status_code == 201

    def test_name_so(self, admin_client):
        r = admin_client.post(CATEGORIES, {"name": "Fruit", "name_so": "Miro"})
        assert r.status_code == 201
        assert r.json()["name_so"] == "Miro"


class TestProducts:
    def test_create(self, admin_client, category, store):
        r = admin_client.post(PRODUCTS, {
            "barcode": "111", "name": "Milk", "price": "1.45", "expected_weight_g": 1035,
            "category": category.id,
        })
        assert r.status_code == 201, r.json()
        body = r.json()
        assert body["category_name"] == "Grocery"
        assert body["weight_tolerance_g"] is None
        assert body["effective_tolerance_g"] == 10
        assert Product.objects.get(pk=body["id"]).store_id == store.id

    def test_no_category(self, admin_client):
        r = admin_client.post(PRODUCTS, {"barcode": "9", "name": "X", "price": "1", "expected_weight_g": 5})
        assert r.status_code == 201
        assert r.json()["category"] is None and r.json()["category_name"] is None

    def test_duplicate_barcode(self, admin_client, product):
        r = admin_client.post(PRODUCTS, {
            "barcode": product.barcode, "name": "Dup", "price": "1.00", "expected_weight_g": 10,
        })
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "VALIDATION_ERROR"
        assert "already exists" in r.json()["error"]["details"]["barcode"][0]
        # updating the product itself keeps its barcode
        assert admin_client.patch(f"{PRODUCTS}{product.id}/", {"barcode": product.barcode}).status_code == 200

    def test_category_of_other_store_rejected(self, admin_client, other_store):
        foreign = Category.objects.create(store=other_store, name="Foreign")
        r = admin_client.post(PRODUCTS, {
            "barcode": "7", "name": "X", "price": "1.00", "expected_weight_g": 10, "category": foreign.id,
        })
        assert r.status_code == 400
        assert "category" in r.json()["error"]["details"]

    def test_filters_search_ordering(self, admin_client, make_product):
        make_product(barcode="1", name="Apple", price="3.00")
        make_product(barcode="2", name="Banana", price="1.00", is_active=False)
        make_product(barcode="3", name="Cherry", price="2.00", requires_weight_check=False)
        names = lambda r: [p["name"] for p in r.json()["results"]]  # noqa: E731
        assert names(admin_client.get(PRODUCTS, {"is_active": "false"})) == ["Banana"]
        assert names(admin_client.get(PRODUCTS, {"requires_weight_check": "false"})) == ["Cherry"]
        assert names(admin_client.get(PRODUCTS, {"search": "err"})) == ["Cherry"]
        assert names(admin_client.get(PRODUCTS, {"ordering": "price"})) == ["Banana", "Cherry", "Apple"]

    def test_scoping(self, other_admin_client, superuser_client, product, store):
        assert other_admin_client.get(PRODUCTS).json()["count"] == 0
        assert other_admin_client.get(f"{PRODUCTS}{product.id}/").status_code == 404
        assert other_admin_client.delete(f"{PRODUCTS}{product.id}/").status_code == 404
        assert superuser_client.get(PRODUCTS).status_code == 400
        assert superuser_client.get(PRODUCTS, {"store": store.id}).json()["count"] == 1

    def test_staff_and_kiosk_forbidden(self, staff_client, kiosk_client):
        assert staff_client.get(PRODUCTS).status_code == 403
        assert kiosk_client.get(PRODUCTS).status_code in (401, 403)

    def test_json_create_with_name_so(self, admin_client):
        r = admin_client.post(PRODUCTS, {
            "barcode": "55", "name": "Rice", "name_so": "Bariis", "price": "1.00", "expected_weight_g": 10,
        }, format="json")
        assert r.status_code == 201, r.json()
        assert r.json()["name_so"] == "Bariis"
        assert r.json()["image_url"] is None
        assert "image" not in r.json()

    def test_multipart_image_upload(self, admin_client, kiosk_client, product, settings, tmp_path):
        settings.MEDIA_ROOT = tmp_path
        buf = io.BytesIO()
        Image.new("RGB", (2, 2), "red").save(buf, format="PNG")
        upload = SimpleUploadedFile("rice.png", buf.getvalue(), content_type="image/png")
        r = admin_client.patch(f"{PRODUCTS}{product.id}/", {"image": upload, "name_so": "Bariis"},
                               format="multipart")
        assert r.status_code == 200, r.json()
        url = r.json()["image_url"]
        assert url.startswith("http://testserver/media/products/") and url.endswith(".png")
        product.refresh_from_db()
        assert product.name_so == "Bariis"
        assert (tmp_path / product.image.name).is_file()
        assert kiosk_client.get(LOOKUP, {"barcode": product.barcode}).json()["image_url"] == url

    def test_non_image_upload_rejected(self, admin_client, product, settings, tmp_path):
        settings.MEDIA_ROOT = tmp_path
        upload = SimpleUploadedFile("x.png", b"not an image", content_type="image/png")
        r = admin_client.patch(f"{PRODUCTS}{product.id}/", {"image": upload}, format="multipart")
        assert r.status_code == 400
        assert "image" in r.json()["error"]["details"]


CSV = (
    "barcode,name,price,expected_weight_g,weight_tolerance_g,category\n"
    "6291041500213,Rice 1kg,2.80,1020,25,Grocery\n"      # updates the existing fixture product
    "100,Milk,1.45,1035,,Dairy\n"                        # new, new category
    "101,Gum,0.50,30,5,\n"                               # new, no category
    ",No barcode,1.00,10,,\n"                            # error
    "102,Bad price,abc,10,,\n"                           # error
    "103,Bad weight,1.00,-5,,\n"                         # error
)


class TestImportExport:
    def _upload(self, client, text, name="p.csv"):
        f = SimpleUploadedFile(name, text.encode("utf-8"), content_type="text/csv")
        return client.post(f"{PRODUCTS}import/", {"file": f}, format="multipart")

    def test_import(self, admin_client, product, store):
        r = self._upload(admin_client, CSV)
        assert r.status_code == 200, r.json()
        body = r.json()
        assert body["created"] == 2
        assert body["updated"] == 1
        assert [(e["row"], e["barcode"]) for e in body["errors"]] == [(5, ""), (6, "102"), (7, "103")]
        product.refresh_from_db()
        assert (product.name, product.expected_weight_g, product.weight_tolerance_g) == ("Rice 1kg", 1020, 25)
        milk = Product.objects.get(store=store, barcode="100")
        assert milk.category.name == "Dairy" and milk.weight_tolerance_g is None
        assert Product.objects.get(store=store, barcode="101").category is None

    def test_import_missing_columns(self, admin_client):
        r = self._upload(admin_client, "barcode,name\n1,x\n")
        assert r.status_code == 400
        assert "file" in r.json()["error"]["details"]

    def test_import_requires_file(self, admin_client):
        assert admin_client.post(f"{PRODUCTS}import/", {}, format="multipart").status_code == 400

    def test_import_service_bom_and_store(self, store, other_store):
        data = "﻿barcode,name,price,expected_weight_g\n1,A,1.00,10\n".encode()
        assert import_products_csv(store, io.BytesIO(data)) == {"created": 1, "updated": 0, "errors": []}
        assert not Product.objects.filter(store=other_store).exists()

    def test_export_round_trip(self, admin_client, other_admin_client, make_product):
        make_product(barcode="1", name="Apple, green", price="3.00", tolerance=7)
        make_product(barcode="2", name="Banana", price="1.00")
        r = admin_client.get(f"{PRODUCTS}export/")
        assert r.status_code == 200
        assert r["Content-Type"].startswith("text/csv")
        assert "attachment" in r["Content-Disposition"]
        text = r.content.decode()
        assert text.splitlines()[0] == "barcode,name,price,expected_weight_g,weight_tolerance_g,category,name_so"
        assert '1,"Apple, green",3.00,500,7,Grocery,' in text
        # re-import into another store reproduces the catalogue
        res = self._upload(other_admin_client, text)
        assert res.json() == {"created": 2, "updated": 0, "errors": []}
        other = other_admin_client.get(f"{PRODUCTS}export/").content.decode()
        assert other == text

    def test_name_so_column(self, admin_client, product, store):
        text = (
            "barcode,name,price,expected_weight_g,weight_tolerance_g,category,name_so\n"
            f"{product.barcode},Rice,2.50,500,,Grocery,Bariis\n"
            "200,Milk,1.45,1035,,,\n"
        )
        assert self._upload(admin_client, text).json() == {"created": 1, "updated": 1, "errors": []}
        product.refresh_from_db()
        assert product.name_so == "Bariis"
        assert Product.objects.get(store=store, barcode="200").name_so is None
        exported = admin_client.get(f"{PRODUCTS}export/").content.decode()
        assert f"{product.barcode},Rice,2.50,500,,Grocery,Bariis" in exported

    def test_import_without_name_so_keeps_existing(self, admin_client, product):
        product.name_so = "Bariis"
        product.save()
        text = f"barcode,name,price,expected_weight_g\n{product.barcode},Rice 2,2.50,500\n"
        assert self._upload(admin_client, text).json() == {"created": 0, "updated": 1, "errors": []}
        product.refresh_from_db()
        assert (product.name, product.name_so) == ("Rice 2", "Bariis")
