import hashlib
import json
from decimal import Decimal

import pytest
import requests
import responses
from responses import matchers

from apps.payments.models import Payment
from apps.payments.providers import edahab as edahab_mod
from apps.payments.providers.edahab import EdahabProvider
from apps.payments.providers.mock import MockProvider
from apps.payments.providers.registry import get_provider
from apps.payments.providers.waafi import WaafiProvider

from .helpers import order_with_items

WAAFI_URL = "https://waafi.test/asm"
EDAHAB_URL = "https://edahab.test/api"
PAID = {"InvoiceStatus": "Paid", "TransactionId": "ED9", "InvoiceId": 77, "StatusCode": 0}


@pytest.fixture
def creds(settings):
    settings.WAAFI_API_URL = WAAFI_URL
    settings.WAAFI_MERCHANT_UID = "M123"
    settings.WAAFI_API_USER_ID = "U1"
    settings.WAAFI_API_KEY = "waafi-secret-key"
    settings.EDAHAB_API_URL = EDAHAB_URL
    settings.EDAHAB_API_KEY = "edahab-secret-key"
    settings.EDAHAB_AGENT_CODE = "A1"
    settings.EDAHAB_SECRET = "shh"
    settings.PAYMENT_HTTP_TIMEOUT_SECONDS = 5
    return settings


@pytest.fixture
def make_payment(kiosk, product):
    def _make(method="WAAFI", phone="252615000001"):
        order = order_with_items(kiosk, product)
        return Payment.objects.create(order=order, method=method, amount=order.total, currency="USD",
                                      customer_phone=phone)
    return _make


class TestMock:
    @pytest.mark.parametrize("outcome,status,message", [
        ("success", "SUCCESS", "Approved"),
        ("fail", "FAILED", "Customer declined"),
        ("timeout", "FAILED", "TIMEOUT"),
    ])
    def test_setting_outcome(self, settings, make_payment, outcome, status, message):
        settings.PAYMENT_MOCK_OUTCOME = outcome
        result = MockProvider().charge(make_payment())
        assert (result.status, result.message) == (status, message)
        if status == "SUCCESS":
            assert result.reference.startswith("MOCK-")

    def test_phone_suffix_overrides_setting(self, settings, make_payment):
        settings.PAYMENT_MOCK_OUTCOME = "success"
        assert MockProvider().charge(make_payment(phone="252610000000")).status == "FAILED"
        assert MockProvider().charge(make_payment(phone="252619999")).message == "TIMEOUT"

    def test_check_status(self, make_payment):
        assert MockProvider().check_status(make_payment()).status == "SUCCESS"
        assert MockProvider().check_status(make_payment(phone="252610000000")).status == "FAILED"
        assert MockProvider().check_status(make_payment(phone="252619999")).status == "FAILED"


class TestRegistry:
    def test_mock_backend_routes_everything_to_mock(self, settings):
        settings.PAYMENT_PROVIDER_BACKEND = "mock"
        assert isinstance(get_provider("WAAFI"), MockProvider)
        assert isinstance(get_provider("EDAHAB"), MockProvider)

    def test_live_backend(self, settings):
        settings.PAYMENT_PROVIDER_BACKEND = "live"
        assert isinstance(get_provider("WAAFI"), WaafiProvider)
        assert isinstance(get_provider("EDAHAB"), EdahabProvider)


@pytest.mark.usefixtures("creds")
class TestWaafi:
    @responses.activate
    def test_success(self, make_payment):
        payment = make_payment()
        responses.post(WAAFI_URL, json={"responseCode": "2001", "responseMsg": "RCS_SUCCESS",
                                        "params": {"transactionId": "TX42", "state": "APPROVED"}})
        result = WaafiProvider().charge(payment)
        assert result.status == "SUCCESS" and result.reference == "TX42"

        sent = json.loads(responses.calls[0].request.body)
        assert sent["serviceName"] == "API_PURCHASE"
        assert sent["serviceParams"]["apiKey"] == "waafi-secret-key"
        assert sent["serviceParams"]["payerInfo"] == {"accountNo": "252615000001"}
        tx = sent["serviceParams"]["transactionInfo"]
        assert tx["referenceId"] == str(payment.id)
        assert tx["amount"] == str(payment.amount)
        # Secret never stored.
        assert result.request["serviceParams"]["apiKey"] == "***"
        assert "waafi-secret-key" not in json.dumps(result.request)

    @responses.activate
    def test_declined(self, make_payment):
        responses.post(WAAFI_URL, json={"responseCode": "5310", "responseMsg": "RCS_USER_REJECTED",
                                        "params": {}})
        result = WaafiProvider().charge(make_payment())
        assert (result.status, result.message) == ("FAILED", "RCS_USER_REJECTED")
        assert result.request["serviceParams"]["apiKey"] == "***"

    @responses.activate
    def test_timeout(self, make_payment):
        responses.post(WAAFI_URL, body=requests.Timeout())
        result = WaafiProvider().charge(make_payment())
        assert (result.status, result.message) == ("FAILED", "TIMEOUT")
        assert result.request["serviceParams"]["apiKey"] == "***"


@pytest.mark.usefixtures("creds")
class TestEdahab:
    @responses.activate
    def test_paid_and_signed(self, make_payment):
        responses.post(f"{EDAHAB_URL}/IssueInvoice", json={
            "InvoiceStatus": "Paid", "TransactionId": "ED1", "InvoiceId": 77, "StatusCode": 0,
            "StatusDescription": "Success"})
        result = EdahabProvider().charge(make_payment(method="EDAHAB"))
        assert result.status == "SUCCESS" and result.reference == "ED1"

        req = responses.calls[0].request
        raw = req.body if isinstance(req.body, str) else req.body.decode()
        expected_hash = hashlib.sha256((raw + "shh").encode()).hexdigest()
        assert f"hash={expected_hash}" in req.url
        body = json.loads(req.body)
        assert body["apiKey"] == "edahab-secret-key" and body["amount"] == 2.5
        assert result.request["apiKey"] == "***"
        assert "edahab-secret-key" not in json.dumps(result.request)

    @responses.activate
    def test_pending_then_paid(self, make_payment, monkeypatch):
        monkeypatch.setattr(EdahabProvider, "poll_interval_seconds", 0.01)
        monkeypatch.setattr(edahab_mod.time, "sleep", lambda s: None)
        responses.post(f"{EDAHAB_URL}/IssueInvoice", json={
            "InvoiceStatus": "Pending", "InvoiceId": 77, "StatusCode": 0})
        responses.post(f"{EDAHAB_URL}/CheckInvoiceStatus", json={
            "InvoiceStatus": "Pending", "InvoiceId": 77, "StatusCode": 0})
        responses.post(f"{EDAHAB_URL}/CheckInvoiceStatus",
                       match=[matchers.json_params_matcher({"invoiceId": 77, "apiKey": "edahab-secret-key"})],
                       json=PAID)
        result = EdahabProvider().charge(make_payment(method="EDAHAB"))
        assert result.status == "SUCCESS" and result.reference == "ED9"
        assert len(responses.calls) == 3

    @responses.activate
    def test_unpaid(self, make_payment):
        responses.post(f"{EDAHAB_URL}/IssueInvoice", json={
            "InvoiceStatus": "Unpaid", "InvoiceId": 78, "StatusCode": 5,
            "StatusDescription": "Insufficient balance"})
        result = EdahabProvider().charge(make_payment(method="EDAHAB"))
        assert (result.status, result.message) == ("FAILED", "Insufficient balance")
        assert result.request["apiKey"] == "***"

    @responses.activate
    def test_timeout(self, make_payment):
        responses.post(f"{EDAHAB_URL}/IssueInvoice", body=requests.Timeout())
        result = EdahabProvider().charge(make_payment(method="EDAHAB"))
        assert (result.status, result.message) == ("FAILED", "TIMEOUT")

    @responses.activate
    def test_check_status_uses_invoice_id(self, make_payment):
        payment = make_payment(method="EDAHAB")
        payment.provider_response = {"InvoiceId": 77}
        responses.post(f"{EDAHAB_URL}/CheckInvoiceStatus", json=PAID)
        assert EdahabProvider().check_status(payment).status == "SUCCESS"
        assert Decimal(payment.amount) == Decimal("2.50")
