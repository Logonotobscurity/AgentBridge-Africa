import asyncio
import hashlib
import hmac
import json
from decimal import Decimal
from uuid import uuid4

import pytest

from agentbridge.core.payment_lifecycle import (
    PaymentStatus,
    PaymentTransaction,
    PostgresPaymentRepository,
    WebhookEvent,
    WebhookIngestResult,
)
from agentbridge.webhooks.handlers import WebhookHandler
from agentbridge.webhooks.redaction import WebhookPayloadSanitizer, WebhookRedactionError
from agentbridge.webhooks.security import PaystackSignatureVerifier

KEY = b"test-only-pii-pseudonymization-key-32-bytes"


class CapturingRepository:
    def __init__(self) -> None:
        self.event = None

    async def ingest_webhook(self, event):
        self.event = event
        return WebhookIngestResult(
            inserted=True,
            matched=True,
            transaction=PaymentTransaction(
                transaction_id=uuid4(),
                run_id="run-redaction",
                idempotency_key="idem-redaction",
                provider="paystack",
                provider_reference=event.provider_reference,
                amount=Decimal("1.00"),
                currency="NGN",
                status=PaymentStatus.CALLBACK_RECEIVED,
            ),
            reconciliation_required=True,
        )


def test_handler_persists_only_minimized_payload_and_raw_body_hash():
    async def scenario():
        secret = b"paystack-webhook-secret"
        raw_payload = {
            "event": "charge.success",
            "data": {
                "reference": "pay-ref-redaction",
                "status": "success",
                "customer": {
                    "phone": "+234 803 123 4567",
                    "email": "Customer@Example.COM",
                    "first_name": "Ada",
                    "metadata": {"address": "sensitive street"},
                },
                "authorization": {"last4": "4081", "bank": "Sensitive Bank"},
            },
        }
        body = json.dumps(raw_payload).encode()
        signature = hmac.new(secret, body, hashlib.sha512).hexdigest()
        repository = CapturingRepository()
        handler = WebhookHandler(
            repository,
            {"paystack": PaystackSignatureVerifier(secret)},
            WebhookPayloadSanitizer(KEY, key_id="2026-09"),
        )

        await handler.handle("paystack", body, {"x-paystack-signature": signature})
        event = repository.event
        assert event.payload_sha256 == hashlib.sha256(body).hexdigest()
        assert event.provider_reference == "pay-ref-redaction"
        assert event.payload["event"] == "charge.success"
        assert event.payload["provider_status"] == "success"

        serialized = json.dumps(event.payload, sort_keys=True)
        for forbidden in (
            "8031234567",
            "Customer@Example.COM",
            "Ada",
            "sensitive street",
            "4081",
            "Sensitive Bank",
            "authorization",
            "metadata",
        ):
            assert forbidden not in serialized
        assert {item["kind"] for item in event.payload["pii_fingerprints"]} == {"email", "msisdn"}

    asyncio.run(scenario())


def test_msisdn_fingerprint_is_normalized_keyed_and_stable():
    first = WebhookPayloadSanitizer(KEY, key_id="v1").sanitize(
        "mpesa",
        {
            "Body": {
                "stkCallback": {
                    "ResultCode": 0,
                    "CallbackMetadata": {
                        "Item": [{"Name": "PhoneNumber", "Value": "+254 712 345 678"}]
                    },
                }
            }
        },
    )
    second = WebhookPayloadSanitizer(KEY, key_id="v1").sanitize(
        "mpesa",
        {
            "Body": {
                "stkCallback": {
                    "ResultCode": "0",
                    "CallbackMetadata": {
                        "Item": [{"Name": "PhoneNumber", "Value": "254712345678"}]
                    },
                }
            }
        },
    )
    rotated = WebhookPayloadSanitizer(b"rotated-test-key-material-at-least-32-bytes", key_id="v2").sanitize(
        "mpesa",
        {
            "Body": {
                "stkCallback": {
                    "ResultCode": 0,
                    "CallbackMetadata": {
                        "Item": [{"Name": "PhoneNumber", "Value": "254712345678"}]
                    },
                }
            }
        },
    )

    first_fingerprint = first["pii_fingerprints"][0]
    assert first_fingerprint["digest"] == second["pii_fingerprints"][0]["digest"]
    assert first_fingerprint["digest"] != rotated["pii_fingerprints"][0]["digest"]
    assert first_fingerprint["algorithm"] == "HMAC-SHA256"
    assert first_fingerprint["key_id"] == "v1"
    assert "254712345678" not in json.dumps(first)


def test_sanitizer_configuration_and_provider_fail_closed():
    with pytest.raises(WebhookRedactionError, match="at least 32 bytes"):
        WebhookPayloadSanitizer(b"short")
    with pytest.raises(WebhookRedactionError, match="AGENTBRIDGE_PII_HMAC_KEY"):
        WebhookPayloadSanitizer.from_environment(environ={})
    with pytest.raises(WebhookRedactionError, match="unsupported webhook provider"):
        WebhookPayloadSanitizer(KEY).sanitize("caller-controlled-provider", {})


def test_postgres_repository_rejects_direct_raw_payload_before_pool_access():
    raw_event = WebhookEvent(
        provider="paystack",
        event_id="raw-event",
        provider_reference="pay-ref",
        payload_sha256="0" * 64,
        payload={"data": {"customer": {"phone": "+2348031234567"}}},
    )

    async def scenario():
        with pytest.raises(ValueError, match="sanitized provider projection"):
            await PostgresPaymentRepository(pool=None).ingest_webhook(raw_event)

    asyncio.run(scenario())
