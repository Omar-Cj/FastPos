import pytest

from apps.orders import services
from apps.stores.models import Kiosk


@pytest.fixture
def order(kiosk):
    return services.open_order(kiosk)


@pytest.fixture
def make_kiosk(db):
    """Extra kiosks; each needs its own token so the unique key_hash doesn't collide."""

    def _make(store, name, serial_number):
        k = Kiosk(store=store, name=name, serial_number=serial_number)
        k.issue_token()
        k.save()
        return k

    return _make


@pytest.fixture
def other_kiosk_client(other_store):
    from rest_framework.test import APIClient

    k = Kiosk(store=other_store, name="Other kiosk", serial_number="SN-OTHER")
    token = k.issue_token()
    k.save()
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Kiosk {token}")
    return client
