"""seed_demo against the test database: Somali names, secondary currency, both staff users, idempotency."""

import io
from decimal import Decimal

import pytest
from django.core.management import call_command

from apps.accounts.models import User
from apps.accounts.services import verify_staff
from apps.catalog.models import Category, Product
from apps.simulator.management.commands.seed_demo import DEMO_PRODUCTS
from apps.stores.models import Kiosk, Store

pytestmark = pytest.mark.django_db


def _seed(*args):
    out = io.StringIO()
    call_command("seed_demo", *args, stdout=out)
    return out.getvalue()


def test_seed_demo_contents():
    out = _seed()
    store = Store.objects.get(name="Demo Mart")
    assert store.secondary_currency == "SLSH"
    assert store.secondary_rate == Decimal("8500")

    products = Product.objects.filter(store=store)
    assert products.count() == len(DEMO_PRODUCTS) == 17
    assert not products.filter(name_so__isnull=True).exists()
    assert not products.filter(name_so="").exists()
    assert products.get(barcode="6291041500220").name_so == "Sonkor 1kg"
    cats = Category.objects.filter(store=store)
    assert cats.count() == 6
    assert not cats.filter(name_so__isnull=True).exists()

    hodan = verify_staff(store, "1001", "1234")
    assert (hodan.first_name, hodan.last_name, hodan.role) == ("Hodan", "Cabdi", User.Role.STAFF)
    assert hodan.email == f"staff-1001@{store.id}.demo.test"
    assert verify_staff(store, "S1", "1234").email == f"staff-s1@{store.id}.demo.test"

    assert "KIOSK_TOKEN=" in out
    tail = out.strip().splitlines()[-2:]
    assert tail == ["Staff: code=S1 pin=1234", "Staff: code=1001 pin=1234 (Hodan Cabdi)"]


def test_seed_demo_idempotent_and_options():
    _seed()
    _seed("--kiosk-staff-code", "2002", "--staff-pin", "4321")
    store = Store.objects.get(name="Demo Mart")
    assert Store.objects.count() == 1
    assert Product.objects.filter(store=store).count() == 17
    assert Kiosk.objects.filter(store=store).count() == 1
    assert verify_staff(store, "2002", "4321").get_full_name() == "Hodan Cabdi"
    assert verify_staff(store, "S1", "4321")
    assert User.objects.filter(store=store, role=User.Role.STAFF).count() == 3  # S1, 1001, 2002
