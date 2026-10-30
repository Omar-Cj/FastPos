"""Create a demo store with a kiosk, an admin, a staff member and realistic products.

    python manage.py seed_demo
Prints the kiosk token (shown once) and the admin / staff credentials.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import User
from apps.catalog.models import Category, Product
from apps.stores.models import Kiosk, Store

# barcode, name, category, price, expected_weight_g, tolerance_g (None = store default), requires_check
DEMO_PRODUCTS = [
    ("6291041500213", "Basmati Rice 1kg", "Grocery", "2.80", 1020, 25, True),
    ("6291041500220", "Sugar 1kg", "Grocery", "1.60", 1010, 20, True),
    ("6291041500237", "Spaghetti 500g", "Grocery", "1.20", 515, None, True),
    ("6291041500244", "Tomato Paste 400g", "Grocery", "0.95", 455, None, True),
    ("6291041500251", "Sunflower Oil 1L", "Grocery", "3.40", 940, 15, True),
    ("6291041500268", "UHT Milk 1L", "Dairy", "1.45", 1035, 15, True),
    ("6291041500275", "Yogurt 500g", "Dairy", "1.10", 530, None, True),
    ("6291041500282", "Cheddar Cheese 250g", "Dairy", "3.90", 262, None, True),
    ("6291041500299", "Chocolate Bar 100g", "Snacks", "1.25", 104, 5, True),
    ("6291041500305", "Chewing Gum", "Snacks", "0.50", 30, 5, True),
    ("6291041500312", "Potato Chips 150g", "Snacks", "1.75", 158, None, True),
    ("6291041500329", "Cola 1.5L", "Drinks", "1.30", 1560, 20, True),
    ("6291041500336", "Water 500ml", "Drinks", "0.35", 520, None, True),
    ("6291041500343", "Shampoo 400ml", "Personal care", "4.20", 445, None, True),
    ("6291041500350", "Perfume 50ml", "Personal care", "18.00", 320, None, True),
    ("6291041500367", "Razor Blades 5pk", "Personal care", "6.50", 48, 5, True),
    ("6291041500374", "Prepaid SIM Card", "Electronics", "2.00", 6, None, False),
]


class Command(BaseCommand):
    help = "Seed a demo store, kiosk, admin, staff and products."

    def add_arguments(self, parser):
        parser.add_argument("--store-name", default="Demo Mart")
        parser.add_argument("--admin-email", default="admin@demo.test")
        parser.add_argument("--admin-password", default="admin1234")
        parser.add_argument("--staff-code", default="S1")
        parser.add_argument("--staff-pin", default="1234")

    @transaction.atomic
    def handle(self, *args, **opts):
        store, created = Store.objects.get_or_create(
            name=opts["store_name"], defaults={"address": "Maka Al-Mukarama Rd, Mogadishu"}
        )
        self.stdout.write(f"Store: {store.name} (id={store.id}) {'created' if created else 'exists'}")

        cats = {}
        for name in {row[2] for row in DEMO_PRODUCTS}:
            cats[name], _ = Category.objects.get_or_create(store=store, name=name)

        n_new = 0
        for barcode, name, cat, price, weight, tol, check in DEMO_PRODUCTS:
            _, was_created = Product.objects.update_or_create(
                store=store,
                barcode=barcode,
                defaults={
                    "name": name,
                    "category": cats[cat],
                    "price": Decimal(price),
                    "expected_weight_g": weight,
                    "weight_tolerance_g": tol,
                    "requires_weight_check": check,
                    "has_eas_tag": True,
                    "is_active": True,
                },
            )
            n_new += int(was_created)
        self.stdout.write(f"Products: {len(DEMO_PRODUCTS)} total, {n_new} new")

        admin, created = User.objects.get_or_create(
            email=opts["admin_email"], defaults={"role": User.Role.ADMIN, "store": store}
        )
        admin.set_password(opts["admin_password"])
        admin.save()
        self.stdout.write(f"Admin: {admin.email} / {opts['admin_password']}")

        staff, _ = User.objects.get_or_create(
            email=f"staff-{opts['staff_code'].lower()}@{store.id}.demo.test",
            defaults={"role": User.Role.STAFF, "store": store, "staff_code": opts["staff_code"]},
        )
        staff.set_pin(opts["staff_pin"])
        staff.save()
        self.stdout.write(f"Staff: code={opts['staff_code']} pin={opts['staff_pin']}")

        kiosk, created = Kiosk.objects.get_or_create(store=store,
            name="Kiosk 1", defaults={"serial_number": "SIM-0001"})
        token = kiosk.issue_token()
        kiosk.save()
        self.stdout.write(self.style.SUCCESS(f"Kiosk: {kiosk.name} (id={kiosk.id})"))
        self.stdout.write(self.style.SUCCESS(f"KIOSK_TOKEN={token}"))
