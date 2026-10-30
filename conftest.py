"""Shared fixtures for every app's tests.

Two API clients exist: `kiosk_client` (device token) and `admin_client` (JWT, ADMIN of `store`).
`other_store` + `other_admin_client` exist to prove store scoping.
"""

from decimal import Decimal

import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import User
from apps.catalog.models import Category, Product
from apps.stores.models import Kiosk, Store


@pytest.fixture
def store(db):
    return Store.objects.create(name="Demo Mart", currency="USD", default_weight_tolerance_g=10)


@pytest.fixture
def other_store(db):
    return Store.objects.create(name="Other Mart", currency="USD", default_weight_tolerance_g=10)


@pytest.fixture
def kiosk(store):
    k = Kiosk(store=store, name="Kiosk 1", serial_number="SN-1")
    k.plain_token = k.issue_token()
    k.save()
    return k


@pytest.fixture
def kiosk_token(kiosk):
    return kiosk.plain_token


@pytest.fixture
def kiosk_client(kiosk_token):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Kiosk {kiosk_token}")
    return client


@pytest.fixture
def anon_client():
    return APIClient()


def _jwt_client(user):
    client = APIClient()
    access = RefreshToken.for_user(user).access_token
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    return client


@pytest.fixture
def admin_user(store):
    return User.objects.create_user(email="admin@demo.test", password="pass1234", role=User.Role.ADMIN, store=store)


@pytest.fixture
def admin_client(admin_user):
    return _jwt_client(admin_user)


@pytest.fixture
def other_admin_client(other_store):
    user = User.objects.create_user(email="admin@other.test",
        password="pass1234", role=User.Role.ADMIN, store=other_store)
    return _jwt_client(user)


@pytest.fixture
def superuser_client(db):
    user = User.objects.create_superuser(email="root@fastpos.test", password="pass1234")
    return _jwt_client(user)


@pytest.fixture
def staff_user(store):
    user = User.objects.create_user(
        email="staff@demo.test", password="pass1234", role=User.Role.STAFF, store=store, staff_code="S1"
    )
    user.set_pin("1234")
    user.save()
    return user


@pytest.fixture
def staff_client(staff_user):
    return _jwt_client(staff_user)


@pytest.fixture
def category(store):
    return Category.objects.create(store=store, name="Grocery")


@pytest.fixture
def make_product(store, category):
    """Factory: make_product(barcode="1", expected_weight_g=500, price="2.50", tolerance=None, ...)."""

    def _make(barcode="6291041500213", name="Rice 500g", expected_weight_g=500, price="2.50",
              tolerance=None, requires_weight_check=True, has_eas_tag=True, store_=None, **extra):
        return Product.objects.create(
            store=store_ or store,
            category=category if (store_ is None or store_ == store) else None,
            barcode=barcode,
            name=name,
            price=Decimal(price),
            expected_weight_g=expected_weight_g,
            weight_tolerance_g=tolerance,
            requires_weight_check=requires_weight_check,
            has_eas_tag=has_eas_tag,
            **extra,
        )

    return _make


@pytest.fixture
def product(make_product):
    return make_product()
