import json
from pathlib import Path

from agentbridge.core.sensitive_data import (
    OMITTED,
    POLICY_VERSION,
    REDACTED,
    redact_sensitive_data,
)
from agentbridge.core.telemetry import Tracer
from evals.trace_hydrator import (
    EvalCase,
    from_langfuse,
    from_otel,
    _attributes,
    publish_to_langfuse,
    write_eval_cases,
)


def _serialized(value):
    return json.dumps(value, sort_keys=True, default=str)


def test_key_normalization_redacts_identity_bank_and_beneficiary_aliases():
    canaries = {
        "BVN": "BVN_CANARY_12345678901",
        "bvnNumber": "BVN_NUMBER_CANARY",
        "national-id-number": "NIN_CANARY_12345678901",
        "NINNumber": "NIN_NUMBER_CANARY",
        "bankAccountNumber": "ACCOUNT_CANARY_0123456789",
        "account_no": "ACCOUNT_NO_CANARY",
        "customerName": "PERSON_CANARY_OLA",
        "beneficiary_name": "BENEFICIARY_CANARY_ADA",
        "streetAddress": "ADDRESS_CANARY_17_TEST_ROAD",
        "emailAddress": "EMAIL_CANARY@example.invalid",
        "phoneNumber": "PHONE_CANARY_2348000000000",
        "bankDetails": {"account": "NESTED_ACCOUNT_CANARY"},
    }

    clean = redact_sensitive_data(canaries)

    assert all(value == REDACTED for value in clean.values())
    serialized = _serialized(clean)
    for canary in (
        "BVN_CANARY_12345678901",
        "BVN_NUMBER_CANARY",
        "NIN_CANARY_12345678901",
        "NIN_NUMBER_CANARY",
        "ACCOUNT_CANARY_0123456789",
        "ACCOUNT_NO_CANARY",
        "PERSON_CANARY_OLA",
        "BENEFICIARY_CANARY_ADA",
        "ADDRESS_CANARY_17_TEST_ROAD",
        "EMAIL_CANARY@example.invalid",
        "PHONE_CANARY_2348000000000",
        "NESTED_ACCOUNT_CANARY",
    ):
        assert canary not in serialized


def test_free_text_and_unclassified_strings_are_omitted_by_default():
    clean = redact_sensitive_data({
        "prompt": "PROMPT_CANARY: user provided NIN 12345678901",
        "context": "CONTEXT_CANARY",
        "rationale": "RATIONALE_CANARY",
        "message": "MESSAGE_CANARY",
        "callbackBody": "CALLBACK_CANARY",
        "unknownProviderField": "UNKNOWN_STRING_CANARY",
        "unknownNumericField": 1234567890123,
        "amount": 2500,
        "currency": "NGN",
        "status": "pending",
        "tool": "execute_payment",
    })

    assert clean["prompt"] == OMITTED
    assert clean["context"] == OMITTED
    assert clean["rationale"] == OMITTED
    assert clean["message"] == OMITTED
    assert clean["callbackBody"] == OMITTED
    assert clean["unknownProviderField"] == OMITTED
    assert clean["unknownNumericField"] == OMITTED
    assert clean["amount"] == 2500
    assert clean["currency"] == "NGN"
    assert clean["status"] == "pending"
    assert clean["tool"] == "execute_payment"
    assert all(
        canary not in _serialized(clean)
        for canary in (
            "PROMPT_CANARY", "CONTEXT_CANARY", "RATIONALE_CANARY",
            "MESSAGE_CANARY", "CALLBACK_CANARY", "UNKNOWN_STRING_CANARY",
        )
    )


def test_telemetry_minimizes_before_span_record_and_otlp_export():
    tracer = Tracer()
    span = tracer.start(
        "payment.execute",
        attributes={
            "tool": "execute_payment",
            "currency": "NGN",
            "amount": 4200,
            "payment.retry_count": 1,
            "payment.max_retries": 3,
            "oauth.scopes": ["payments:execute", "admin:all"],
            "customer": {"firstName": "PERSON_CANARY", "BVN": "BVN_CANARY"},
            "message": "TRACE_MESSAGE_CANARY",
            "unknown": "TRACE_UNKNOWN_CANARY",
        },
    )

    # Mutating the public span after creation must not bypass sink sanitization.
    span.attributes["prompt"] = "LATE_PROMPT_CANARY"
    record = span.to_record().model_dump(mode="json")
    otlp = tracer.export_otlp()

    for output in (record, otlp):
        serialized = _serialized(output)
        for canary in (
            "PERSON_CANARY", "BVN_CANARY", "TRACE_MESSAGE_CANARY",
            "TRACE_UNKNOWN_CANARY", "LATE_PROMPT_CANARY",
        ):
            assert canary not in serialized

    assert record["attributes"]["amount"] == 4200
    assert record["attributes"]["currency"] == "NGN"
    assert record["attributes"]["payment.retry_count"] == 1
    assert record["attributes"]["payment.max_retries"] == 3
    assert record["attributes"]["oauth.scopes"] == ["payments:execute"]
    assert record["attributes"]["privacy_policy_version"] == POLICY_VERSION
    exported_span = otlp["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert exported_span["name"] == "payment.execute"


def test_otlp_hydration_cleans_nested_attributes_and_records_policy_version():
    arguments = {
        "amount": 1800,
        "currency": "NGN",
        "idempotency_key": "IDEMPOTENCY_CANARY",
        "bvnNumber": "BVN_CANARY",
        "recipient": "RECIPIENT_CANARY",
        "freeform": "ARGUMENT_TEXT_CANARY",
        "notes": "NOTES_CANARY",
    }
    payload = {
        "resourceSpans": [{
            "scopeSpans": [{
                "spans": [{
                    "traceId": "a" * 32,
                    "spanId": "b" * 16,
                    "name": "payment.execute",
                    "attributes": [
                        {"key": "tool.name", "value": {"stringValue": "execute_payment"}},
                        {"key": "tool.arguments", "value": {"stringValue": json.dumps(arguments)}},
                        {"key": "customer.nin", "value": {"stringValue": "NIN_ATTRIBUTE_CANARY"}},
                        {"key": "prompt", "value": {"stringValue": "OTEL_PROMPT_CANARY"}},
                    ],
                }]
            }]
        }]
    }

    cases = from_otel(payload)
    assert len(cases) == 1
    case = cases[0]
    serialized = _serialized(case.model_dump(mode="json"))

    for canary in (
        "IDEMPOTENCY_CANARY", "BVN_CANARY", "RECIPIENT_CANARY",
        "ARGUMENT_TEXT_CANARY", "NOTES_CANARY",
        "NIN_ATTRIBUTE_CANARY", "OTEL_PROMPT_CANARY",
    ):
        assert canary not in serialized
    assert case.input["tool"] == "execute_payment"
    assert case.input["arguments"]["amount"] == 1800
    assert case.input["arguments"]["currency"] == "NGN"
    assert case.input["arguments"]["bvnNumber"] == REDACTED
    assert case.metadata["privacy_policy_version"] == POLICY_VERSION
    assert case.source_trace_id == "a" * 32
    assert case.source_observation_id == "b" * 16


def test_langfuse_input_output_and_metadata_are_minimized():
    traces = [{
        "id": "c" * 32,
        "name": "payment.execute",
        "input": {
            "tool": "execute_payment",
            "arguments": {
                "amount": 700,
                "currency": "NGN",
                "customerName": "LANGFUSE_NAME_CANARY",
                "ninNumber": "LANGFUSE_NIN_CANARY",
                "freeText": "LANGFUSE_FREE_TEXT_CANARY",
            },
            "prompt": "LANGFUSE_PROMPT_CANARY",
        },
        "output": {
            "status": "success",
            "message": "LANGFUSE_OUTPUT_CANARY",
            "beneficiary": {"name": "LANGFUSE_BENEFICIARY_CANARY"},
        },
        "metadata": {
            "retry_count": 1,
            "notes": "LANGFUSE_METADATA_CANARY",
        },
        "tags": ["LANGFUSE_TAG_CANARY"],
    }]

    cases = from_langfuse(traces)
    assert len(cases) == 1
    serialized = _serialized(cases[0].model_dump(mode="json"))
    for canary in (
        "LANGFUSE_NAME_CANARY", "LANGFUSE_NIN_CANARY",
        "LANGFUSE_FREE_TEXT_CANARY", "LANGFUSE_PROMPT_CANARY",
        "LANGFUSE_OUTPUT_CANARY", "LANGFUSE_BENEFICIARY_CANARY",
        "LANGFUSE_METADATA_CANARY", "LANGFUSE_TAG_CANARY",
    ):
        assert canary not in serialized
    assert cases[0].input["arguments"]["amount"] == 700
    assert cases[0].expected_output["status"] == "success"
    assert cases[0].metadata["privacy_policy_version"] == POLICY_VERSION


class RecordingLangfuseClient:
    def __init__(self):
        self.items = []

    def create_dataset_item(self, **kwargs):
        self.items.append(kwargs)


def test_jsonl_and_langfuse_publication_reapply_policy_at_sink(tmp_path: Path):
    case = EvalCase(
        id="otel_" + ("d" * 32),
        source="otel",
        input={
            "tool": "execute_payment",
            "arguments": {
                "amount": 990,
                "currency": "NGN",
                "accountNumber": "SINK_ACCOUNT_CANARY",
                "unknown": "SINK_UNKNOWN_CANARY",
            },
            "prompt": "SINK_PROMPT_CANARY",
        },
        expected_output={"message": "SINK_OUTPUT_CANARY", "pass": True},
        metadata={"context": "SINK_CONTEXT_CANARY", "score": 1.0},
        source_trace_id="d" * 32,
        source_observation_id="e" * 16,
    )

    path = write_eval_cases([case], tmp_path / "dataset.jsonl")
    jsonl = path.read_text(encoding="utf-8")

    client = RecordingLangfuseClient()
    assert publish_to_langfuse([case], client, "assurance-canary") == 1
    published = _serialized(client.items)

    for output in (jsonl, published):
        for canary in (
            "SINK_ACCOUNT_CANARY", "SINK_UNKNOWN_CANARY",
            "SINK_PROMPT_CANARY", "SINK_OUTPUT_CANARY",
            "SINK_CONTEXT_CANARY",
        ):
            assert canary not in output

    stored = json.loads(jsonl)
    assert stored["input"]["arguments"]["amount"] == 990
    assert stored["metadata"]["score"] == 1.0
    assert client.items[0]["input"]["arguments"]["accountNumber"] == REDACTED


def test_untrusted_identifiers_are_not_retained_as_lineage():
    cases = from_langfuse([{
        "id": "CUSTOMER_NAME_SHOULD_NOT_BE_A_TRACE_ID",
        "name": "payment.execute",
        "input": {"tool": "execute_payment", "arguments": {"amount": 100, "currency": "NGN"}},
        "output": {"status": "success"},
    }])
    assert len(cases) == 1
    assert cases[0].source_trace_id == ""
    serialized = _serialized(cases[0].model_dump(mode="json"))
    assert "CUSTOMER_NAME_SHOULD_NOT_BE_A_TRACE_ID" not in serialized



def test_authorization_scope_and_assurance_score_fields_survive_policy():
    clean = redact_sensitive_data({
        "oauth.scopes": ["payments:execute", "payments:read", "admin:all"],
        "payment.retry_count": 2,
        "payment.max_retries": 3,
        "score": {
            "tool_argument_accuracy": 1.0,
            "execution_grounding": 1.0,
            "failure_retry_behavior": 0.5,
            "pass": True,
        },
    })

    assert clean["oauth.scopes"] == ["payments:execute", "payments:read"]
    assert clean["payment.retry_count"] == 2
    assert clean["payment.max_retries"] == 3
    assert clean["score"]["tool_argument_accuracy"] == 1.0
    assert clean["score"]["execution_grounding"] == 1.0
    assert clean["score"]["failure_retry_behavior"] == 0.5
    assert clean["score"]["pass"] is True


def test_otlp_array_value_scopes_are_decoded_as_strings():
    attrs = _attributes([
        {
            "key": "oauth.scopes",
            "value": {
                "arrayValue": {
                    "values": [
                        {"stringValue": "payments:execute"},
                        {"stringValue": "payments:read"},
                    ]
                }
            },
        }
    ])

    assert attrs["oauth.scopes"] == ["payments:execute", "payments:read"]


def test_langfuse_uuid_trace_id_is_retained_as_linkable_lineage():
    trace_id = "12345678-1234-5678-9abc-def012345678"
    cases = from_langfuse([{
        "id": trace_id,
        "name": "payment.execute",
        "input": {"tool": "execute_payment", "arguments": {"amount": 100, "currency": "NGN"}},
        "output": {"status": "success"},
    }])

    assert len(cases) == 1
    serialized = _serialized(cases[0].model_dump(mode="json"))
    assert trace_id in serialized
    assert f"langfuse_{trace_id}" in serialized



def test_numeric_opaque_lookalike_is_not_retained_as_trace_lineage():
    clean = redact_sensitive_data({"trace_id": "1234567890123456"})
    assert clean["trace_id"] == OMITTED
