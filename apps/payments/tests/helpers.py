from decimal import Decimal

from apps.orders.models import Order, OrderItem


def order_with_items(kiosk, *products):
    """Build an OPEN order with one OrderItem per product via the ORM (no orders API)."""
    order = Order.objects.create(store=kiosk.store, kiosk=kiosk)
    total = Decimal("0")
    for product in products:
        OrderItem.objects.create(
            order=order, product=product, barcode=product.barcode, product_name=product.name,
            unit_price=product.price, expected_weight_g=product.expected_weight_g, tolerance_g=10,
            measured_weight_g=product.expected_weight_g, weight_status=OrderItem.WeightStatus.PASSED,
        )
        total += product.price
    order.subtotal = order.total = total
    order.save()
    return order
