"""Drive one full checkout against a running server, like a real kiosk would.

    python manage.py simulate_checkout --token $KIOSK_TOKEN --scenario mixed --items 6 --override --seed 7

Talks HTTP only (no DB access), so it can target any environment.
"""

import os
import random
import time

from django.core.management.base import BaseCommand, CommandError

from apps.simulator.kiosk_client import KioskAPIError, KioskClient
from apps.simulator.management.commands.seed_demo import DEMO_PRODUCTS
from apps.simulator.scale import SimulatedScale

PERFUME_BARCODE = "6291041500350"
PERFUME_WEIGHT_G = 320
SWAP_FOR_PERFUME_G = 1020  # someone scans the perfume and bags a bag of rice
MIXED_SCENARIOS = ["honest", "swap", "double", "empty", "drift"]
MIXED_WEIGHTS = [5, 2, 1, 1, 1]


class Command(BaseCommand):
    help = "Simulate a kiosk checkout (scan, weigh, pay, print receipt) against a live FastPOS server."

    def add_arguments(self, parser):
        parser.add_argument("--base-url", default="http://127.0.0.1:8000")
        parser.add_argument("--token", default=os.environ.get("KIOSK_TOKEN"),
                            help="Kiosk device token (default: $KIOSK_TOKEN).")
        parser.add_argument("--scenario", default="honest",
                            choices=["honest", "swap", "double", "empty", "drift", "mixed"])
        parser.add_argument("--items", type=int, default=5)
        parser.add_argument("--barcodes", default="", help="Comma list; default: the seed_demo products.")
        parser.add_argument("--pay", default="WAAFI", choices=["WAAFI", "EDAHAB"])
        parser.add_argument("--phone", default="252611111111",
                            help="Mock provider: ...0000 fails, ...9999 times out.")
        parser.add_argument("--override", action="store_true", help="Staff-approve failed weighs.")
        parser.add_argument("--staff-code", default="S1")
        parser.add_argument("--pin", default="1234")
        parser.add_argument("--seed", type=int, default=None)
        parser.add_argument("--drift-g", type=int, default=40)
        parser.add_argument("--poll-interval", type=float, default=1.0,
                            help="Seconds between payment status polls.")
        parser.add_argument("--poll-timeout", type=float, default=60.0)

    def handle(self, *args, **o):
        if not o["token"]:
            raise CommandError("Pass --token or set KIOSK_TOKEN (printed by `manage.py seed_demo`).")
        if o["items"] < 1:
            raise CommandError("--items must be >= 1")
        self.opts = o
        self.rng = random.Random(o["seed"])
        self.scale = SimulatedScale(seed=o["seed"])
        self.client = KioskClient(o["base_url"], o["token"])
        try:
            self._run()
        except KioskAPIError as exc:
            detail = f" {exc.details}" if exc.details else ""
            raise CommandError(f"API error {exc.status} {exc.code}: {exc.message}{detail}") from exc

    # ------------------------------------------------------------------------------------------
    def _barcodes(self):
        if self.opts["barcodes"]:
            codes = [c.strip() for c in self.opts["barcodes"].split(",") if c.strip()]
        else:
            codes = [row[0] for row in DEMO_PRODUCTS]
        return [self.rng.choice(codes) for _ in range(self.opts["items"])]

    def _scenario(self):
        if self.opts["scenario"] == "mixed":
            return self.rng.choices(MIXED_SCENARIOS, weights=MIXED_WEIGHTS)[0]
        return self.opts["scenario"]

    def _run(self):
        o, c = self.opts, self.client
        _, hb = c.heartbeat()
        store = hb["store"]
        self.stdout.write(f"[KIOSK] {hb['kiosk']['name']} @ {store['name']} ({store['currency']})")
        _, order = c.open_order()
        order_id = order["id"]
        self.stdout.write(f"[ORDER] {order_id}")

        added = failed = overridden = 0
        total = "0.00"
        for barcode in self._barcodes():
            _, product = c.lookup(barcode)
            scenario = self._scenario()
            swap = SWAP_FOR_PERFUME_G if product["barcode"] == PERFUME_BARCODE else PERFUME_WEIGHT_G
            measured = self.scale.read(product["expected_weight_g"], scenario=scenario, swap_weight_g=swap,
                                       drift_g=o["drift_g"])
            status, body = c.add_item(order_id, barcode, measured)
            prefix = (f"[SCAN] {product['name']:<22} expected={product['expected_weight_g']} "
                      f"measured={measured} tol={product['tolerance_g']} ({scenario})")
            if status == 422:
                failed += 1
                d = body["error"]["details"]
                self.stdout.write(self.style.WARNING(f"{prefix} → FAIL (delta {d['delta_g']:+d})"))
                if o["override"]:
                    _, res = c.override(order_id, d["verification_id"], o["staff_code"], o["pin"],
                                        reason=f"simulator: {scenario}")
                    overridden += 1
                    added += 1
                    total = res["order"]["total"]
                    self.stdout.write(f"[OVERRIDE] by {o['staff_code']}")
                continue
            added += 1
            total = body["order"]["total"]
            verdict = "SKIPPED (no weight check)" if body["item"]["weight_status"] == "SKIPPED" else "PASS"
            self.stdout.write(self.style.SUCCESS(f"{prefix} → {verdict}"))

        if added == 0:
            c.cancel(order_id, reason="simulator: nothing added")
            self.stdout.write(self.style.WARNING("[ORDER] no items added; order cancelled"))
            self._summary(added, failed, overridden, total, None, "CANCELLED")
            return

        _, payment = c.pay(order_id, o["pay"], o["phone"])
        self.stdout.write(f"[PAY] {o['pay']} {o['phone']} amount={total} → {payment['status']}")
        payment = self._poll(order_id, payment)
        if payment["status"] != "SUCCESS":
            reason = payment.get("failure_reason") or ""
            self.stdout.write(self.style.ERROR(f"[PAY] {payment['status']} {reason}".rstrip()))
            self._summary(added, failed, overridden, total, None, payment["status"])
            return

        self.stdout.write(self.style.SUCCESS("[PAY] SUCCESS"))
        _, receipt = c.receipt(order_id)
        self._print_receipt(receipt)
        c.event(order_id, "RECEIPT_PRINTED")
        c.event(order_id, "EAS_DEACTIVATED")
        self.stdout.write("[EVENT] RECEIPT_PRINTED, EAS_DEACTIVATED")
        self._summary(added, failed, overridden, receipt.get("total", total), receipt.get("receipt_number"),
                      "SUCCESS")

    def _poll(self, order_id, payment):
        deadline = time.monotonic() + self.opts["poll_timeout"]
        while payment["status"] == "PENDING":
            if time.monotonic() >= deadline:
                raise CommandError(
                    f"Payment {payment['id']} still PENDING after {self.opts['poll_timeout']:.0f}s"
                )
            time.sleep(self.opts["poll_interval"])
            _, payment = self.client.payment_status(order_id, payment["id"])
            self.stdout.write(f"[PAY] status={payment['status']}")
        return payment

    def _print_receipt(self, receipt):
        width = 40
        store = receipt.get("store")
        title = store.get("name", "RECEIPT") if isinstance(store, dict) else "RECEIPT"
        lines = ["-" * width, f"{title:^{width}}", f"Receipt #{receipt.get('receipt_number', '?')}"]
        for item in receipt.get("lines") or receipt.get("items") or []:
            name = str(item.get("product_name") or item.get("name") or "?")[:28]
            price = str(item.get("line_total") or item.get("unit_price") or item.get("price") or "")
            qty = item.get("quantity")
            label = f"{name} x{qty}" if qty and qty != 1 else name
            lines.append(f"{label:<30}{price:>10}")
        lines.append("-" * width)
        for key in ("subtotal", "tax", "total"):
            if key in receipt:
                lines.append(f"{key.upper():<30}{receipt[key]!s:>10}")
        lines.append("-" * width)
        for line in lines:
            self.stdout.write(line)

    def _summary(self, added, failed, overridden, total, receipt_number, outcome):
        self.stdout.write(
            f"[SUMMARY] items_added={added} failed={failed} overridden={overridden} total={total} "
            f"receipt={receipt_number if receipt_number is not None else '-'} payment={outcome}"
        )
