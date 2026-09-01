import asyncio
from decimal import Decimal

from pydantic import ValidationError

from agentbridge.payments import (
    CapabilityPackRegistry,
    ConnectorStatus,
    Country,
    Currency,
    DarajaConnector,
    Environment,
    MtnMomoConnector,
    PaymentIntent,
    PaystackConnector,
    ProductionPaymentEngine,
    RuntimeSecretsManager,
    deterministic_reference,
    normalize_ke_msisdn,
)
from agentbridge.core.state import ContextProfile
from agentbridge.payments.models import ConnectorResult
from agentbridge.payments.runtime import HTTPResponse, MappingRecipientResolver, TransportError


class FakeTransport:
    def __init__(self, responses=None, error=False):
        self.responses = list(responses or [])
        self.error = error
        self.calls = []

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.error:
            raise TransportError("redacted")
        return self.responses.pop(0)


def _intent(**updates):
    values = {
        "amount": "100.00",
        "currency": Currency.KES,
        "source_country": Country.KE,
        "destination_country": Country.KE,
        "recipient_ref": "recipient-token-1",
        "merchant_reference": "order-1",
        "idempotency_key_ref": "idem-key-123",
    }
    values.update(updates)
    return PaymentIntent(**values)


def test_payment_intent_forbids_binary_float_amounts():
    try:
        _intent(amount=100.1)
        raise AssertionError("float must not cross provider boundary")
    except ValidationError:
        pass
    assert _intent().amount == Decimal("100.00")


def test_runtime_secrets_are_resolved_per_call_and_redacted():
    manager = RuntimeSecretsManager({"PAYSTACK_SECRET_KEY": "sk_live_private"})
    secrets = manager.resolve("paystack", ("secret_key",))
    assert "sk_live_private" not in repr(secrets)
    assert secrets.reveal("secret_key") == "sk_live_private"


def test_daraja_stk_uses_allowlisted_url_and_normalized_input():
    async def scenario():
        transport = FakeTransport(
            [
                HTTPResponse(200, {"access_token": "short-lived-token"}),
                HTTPResponse(200, {"ResponseCode": "0", "CheckoutRequestID": "ws_CO_1"}),
            ]
        )
        secrets = RuntimeSecretsManager(
            {
                "DARAJA_CONSUMER_KEY": "consumer",
                "DARAJA_CONSUMER_SECRET": "secret",
                "DARAJA_PASSKEY": "passkey",
                "DARAJA_SHORTCODE": "123456",
            }
        )
        connector = DarajaConnector(
            callback_url="https://payments.example.com/webhooks/mpesa",
            callback_hosts=frozenset({"payments.example.com"}),
            recipient_resolver=MappingRecipientResolver({"recipient-token-1": "0712345678"}),
            environment=Environment.SANDBOX,
            transport=transport,
            secrets=secrets,
        )
        result = await connector.initiate_payment(_intent())
        assert result.status == ConnectorStatus.SUBMITTED
        assert result.provider_reference == "ws_CO_1"
        assert transport.calls[1][1].startswith("https://sandbox.safaricom.co.ke/")
        payload = transport.calls[1][2]["json"]
        assert payload["PhoneNumber"] == "254712345678"
        assert payload["Amount"] == "100"

    asyncio.run(scenario())
    assert normalize_ke_msisdn("+254 712 345 678") == "254712345678"


def test_callback_host_cannot_be_caller_controlled():
    try:
        DarajaConnector(
            callback_url="https://169.254.169.254/credentials",
            callback_hosts=frozenset({"payments.example.com"}),
            recipient_resolver=MappingRecipientResolver({}),
        )
        raise AssertionError("SSRF callback URL must fail")
    except ValueError:
        pass


def test_paystack_transport_ambiguity_is_non_terminal():
    async def scenario():
        connector = PaystackConnector(
            recipient_resolver=MappingRecipientResolver({"recipient-token-1": "payer@example.com"}),
            transport=FakeTransport(error=True),
            secrets=RuntimeSecretsManager({"PAYSTACK_SECRET_KEY": "sk_live_private"}),
        )
        result = await connector.initiate_payment(
            _intent(
                currency=Currency.NGN,
                source_country=Country.NG,
                destination_country=Country.NG,
            )
        )
        assert result.status == ConnectorStatus.PENDING
        assert result.requires_reconciliation is True

    asyncio.run(scenario())


def test_mtn_reference_is_deterministic_across_restarts():
    expected = deterministic_reference("idem-key-123")
    assert expected == deterministic_reference("idem-key-123")

    async def scenario():
        transport = FakeTransport(
            [HTTPResponse(200, {"access_token": "token"}), HTTPResponse(202, {})]
        )
        connector = MtnMomoConnector(
            recipient_resolver=MappingRecipientResolver({"recipient-token-1": "233240000000"}),
            environment=Environment.SANDBOX,
            transport=transport,
            secrets=RuntimeSecretsManager(
                {
                    "MTN_MOMO_SUBSCRIPTION_KEY": "subscription",
                    "MTN_MOMO_API_USER": "00000000-0000-0000-0000-000000000001",
                    "MTN_MOMO_API_KEY": "api-key",
                }
            ),
        )
        result = await connector.initiate_payment(
            _intent(
                currency=Currency.GHS,
                source_country=Country.GH,
                destination_country=Country.GH,
            )
        )
        assert result.provider_reference == expected
        assert transport.calls[1][2]["headers"]["X-Reference-Id"] == expected

    asyncio.run(scenario())


def test_production_engine_routes_without_model_selected_provider():
    class Adapter:
        async def initiate_payment(self, intent):
            return ConnectorResult(
                provider="daraja",
                status="SUBMITTED",
                provider_reference="ws_CO_1",
            )

        async def query_transaction_status(self, provider_reference):
            raise AssertionError("not used")

    async def scenario():
        registry = CapabilityPackRegistry()
        registry.register("ke-payments", Adapter())
        engine = ProductionPaymentEngine(registry)
        profile = ContextProfile(locale="en-KE", currency="KES", payment_rails=["mpesa"])
        selection, result = await engine.initiate(profile, _intent())
        assert selection.rail == "mpesa"
        assert result.provider == "daraja"

    asyncio.run(scenario())


def test_capability_registry_rejects_dynamic_import_ids():
    registry = CapabilityPackRegistry()
    try:
        registry.register("evil.module:Connector", object())
        raise AssertionError("dynamic adapter IDs must fail")
    except ValueError:
        pass
