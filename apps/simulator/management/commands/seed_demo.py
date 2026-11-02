"""Create a demo store with a kiosk, an admin, two staff members and realistic products.

    python manage.py seed_demo
Prints the kiosk token (shown once) and the admin / staff credentials.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import User
from apps.catalog.models import Category, Product
from apps.stores.models import Kiosk, Store

# barcode, name, name_so, category, price, expected_weight_g, tolerance_g (None = store default), requires_check
DEMO_PRODUCTS = [
    ("6291041500213", "Basmati Rice 1kg", "Bariis Basmati 1kg", "Grocery", "2.80", 1020, 25, True),
    ("6291041500220", "Sugar 1kg", "Sonkor 1kg", "Grocery", "1.60", 1010, 20, True),
    ("6291041500237", "Spaghetti 500g", "Baasto Isbageeti 500g", "Grocery", "1.20", 515, None, True),
    ("6291041500244", "Tomato Paste 400g", "Salsada Yaanyada 400g", "Grocery", "0.95", 455, None, True),
    ("6291041500251", "Sunflower Oil 1L", "Saliid Gabbaldaye 1L", "Grocery", "3.40", 940, 15, True),
    ("6291041500268", "UHT Milk 1L", "Caano UHT 1L", "Dairy", "1.45", 1035, 15, True),
    ("6291041500275", "Yogurt 500g", "Caano Fadhi 500g", "Dairy", "1.10", 530, None, True),
    ("6291041500282", "Cheddar Cheese 250g", "Farmaajo Cheddar 250g", "Dairy", "3.90", 262, None, True),
    ("6291041500299", "Chocolate Bar 100g", "Shukulaato 100g", "Snacks", "1.25", 104, 5, True),
    ("6291041500305", "Chewing Gum", "Xanjo", "Snacks", "0.50", 30, 5, True),
    ("6291041500312", "Potato Chips 150g", "Jibis Baradho 150g", "Snacks", "1.75", 158, None, True),
    ("6291041500329", "Cola 1.5L", "Kola 1.5L", "Drinks", "1.30", 1560, 20, True),
    ("6291041500336", "Water 500ml", "Biyo 500ml", "Drinks", "0.35", 520, None, True),
    ("6291041500343", "Shampoo 400ml", "Shaambo 400ml", "Personal care", "4.20", 445, None, True),
    ("6291041500350", "Perfume 50ml", "Cadar 50ml", "Personal care", "18.00", 320, None, True),
    ("6291041500367", "Razor Blades 5pk", "Shafraddo Xiirasho 5 xabbo", "Personal care", "6.50", 48, 5, True),
    ("6291041500374", "Prepaid SIM Card", "Kaarka SIM-ka", "Electronics", "2.00", 6, None, False),
]

# category name -> Somali name
DEMO_CATEGORIES_SO = {
    "Grocery": "Raashin",
    "Dairy": "Waxyaabaha Caanaha",
    "Snacks": "Cuntooyin Fudud",
    "Drinks": "Cabitaanno",
    "Personal care": "Daryeelka Jirka",
    "Electronics": "Elektaroonig",
}

SECONDARY_CURRENCY = "SLSH"
SECONDARY_RATE = Decimal("8500")


class Command(BaseCommand):
    help = "Seed a demo store, kiosk, admin, staff and products."

    def add_arguments(self, parser):
        parser.add_argument("--store-name", default="Demo Mart")
        parser.add_argument("--admin-email", default="admin@demo.test")
        parser.add_argument("--admin-password", default="admin1234")
        parser.add_argument("--staff-code", default="S1")
        parser.add_argument("--staff-pin", default="1234")
        parser.add_argument("--kiosk-staff-code", default="1001",
                            help="Numeric code for the kiosk keypad (second staff user, same PIN).")

    @transaction.atomic
    def handle(self, *args, **opts):
        store, created = Store.objects.get_or_create(
            name=opts["store_name"], defaults={"address": "Maka Al-Mukarama Rd, Mogadishu"}
        )
        store.secondary_currency = SECONDARY_CURRENCY
        store.secondary_rate = SECONDARY_RATE
        store.save(update_fields=["secondary_currency", "secondary_rate", "updated_at"])
        self.stdout.write(f"Store: {store.name} (id={store.id}) {'created' if created else 'exists'}")

        cats = {}
        for name in {row[3] for row in DEMO_PRODUCTS}:
            cats[name], _ = Category.objects.update_or_create(
                store=store, name=name, defaults={"name_so": DEMO_CATEGORIES_SO[name]}
            )

        n_new = 0
        for barcode, name, name_so, cat, price, weight, tol, check in DEMO_PRODUCTS:
            _, was_created = Product.objects.update_or_create(
                store=store,
                barcode=barcode,
                defaults={
                    "name": name,
                    "name_so": name_so,
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

        staff_lines = []
        for code, extra in ((opts["staff_code"], {}),
                            (opts["kiosk_staff_code"], {"first_name": "Hodan", "last_name": "Cabdi"})):
            staff, _ = User.objects.update_or_create(
                email=f"staff-{code.lower()}@{store.id}.demo.test",
                defaults={"role": User.Role.STAFF, "store": store, "staff_code": code, **extra},
            )
            staff.set_pin(opts["staff_pin"])
            staff.save()
            name = staff.get_full_name()
            staff_lines.append(f"Staff: code={code} pin={opts['staff_pin']}" + (f" ({name})" if name else ""))

        kiosk, created = Kiosk.objects.get_or_create(store=store,
            name="Kiosk 1", defaults={"serial_number": "SIM-0001"})
        token = kiosk.issue_token()
        kiosk.save()
        self.stdout.write(self.style.SUCCESS(f"Kiosk: {kiosk.name} (id={kiosk.id})"))
        self.stdout.write(self.style.SUCCESS(f"KIOSK_TOKEN={token}"))
        for line in staff_lines:
            self.stdout.write(line)
