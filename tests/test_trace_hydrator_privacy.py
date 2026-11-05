"""Regression tests for the trace-to-evaluation privacy boundary."""

import json

from evals.trace_hydrator import (
    REDACTION_POLICY_VERSION,
    _redact,
    from_langfuse,
    from_otel,
    publish_to_langfuse,
    write_eval_cases,
)


CANARIES = (
    "BVN-CANARY-301",
    "NIN-CANARY-302",
    "ACCOUNT-CANARY-303",
    "CUSTOMER-CANARY-304",
    "BENEFICIARY-CANARY-305",
    "PHONE-CANARY-306",
    "TOKEN-CANARY-307",
    "FREE-TEXT-CANARY-308",
    "SPAN-LABEL-CANARY-309",
)


def _serialized(value):
    return json.dumps(value, sort_keys=True, default=str)


def _assert_no_canaries(value):
    payload = _serialized(value)
    for canary in CANARIES:
        assert canary not in payload


def test_redaction_normalizes_sensitive_aliases_and_drops_free_text():
    raw = {
        "BVN": CANARIES[0],
        "B.V.N.": CANARIES[0],
        "ninNumber": CANARIES[1],
        "bank-account-number": CANARIES[2],
        "bankDetails": CANARIES[2],
        "customerName": CANARIES[3],
        "profile": [
            {"beneficiary_name": CANARIES[4]},
            {"phone-number": CANARIES[5]},
        ],
        "accessToken": CANARIES[6],
        "callback_body": {"raw": CANARIES[7]},
        "prompt": CANARIES[7],
        "amount_minor": 12500,
        "currency": "NGN",
        "spanId": "opaque-span-id",
    }

    cleaned = _redact(raw)

    _assert_no_canaries(cleaned)
    assert cleaned["amount_minor"] == 12500
    assert cleaned["currency"] == "NGN"
    assert cleaned["spanId"] == "opaque-span-id"
    assert cleaned["BVN"] == "[REDACTED]"
    assert cleaned["B.V.N."] == "[REDACTED]"
    assert cleaned["bankDetails"] == "[REDACTED]"
    assert cleaned["ninNumber"] == "[REDACTED]"
    assert cleaned["bank-account-number"] == "[REDACTED]"
    assert cleaned["profile"][0]["beneficiary_name"] == "[REDACTED]"
    assert cleaned["profile"][1]["phone-number"] == "[REDACTED]"
    assert "prompt" not in cleaned
    assert "callback_body" not in cleaned
    assert cleaned["accessToken"] == "[REDACTED]"


def test_otel_hydration_does_not_export_pii_or_dynamic_span_name(tmp_path):
    payload = {
        "spans": [
            {
                "spanId": "opaque-span-id",
                "traceId": "opaque-trace-id",
                "name": f"payment {CANARIES[8]}",
                "attributes": [
                    {
                        "key": "tool.arguments",
                        "value": {
                            "stringValue": json.dumps(
                                {
                                    "BVN": CANARIES[0],
                                    "customerName": CANARIES[3],
                                    "notes": CANARIES[7],
                                    "amount_minor": 500,
                                    "currency": "NGN",
                                }
                            )
                        },
                    }
                ],
            }
        ]
    }

    cases = from_otel(payload)
    assert len(cases) == 1
    assert cases[0].metadata["redaction_policy_version"] == REDACTION_POLICY_VERSION
    _assert_no_canaries(cases)
    assert cases[0].input["arguments"]["amount_minor"] == 500

    jsonl_path = write_eval_cases(cases, tmp_path / "evals" / "safe.jsonl")
    jsonl = jsonl_path.read_text(encoding="utf-8")
    _assert_no_canaries(jsonl)


def test_langfuse_hydration_and_dataset_publication_are_redacted(tmp_path):
    traces = [
        {
            "id": "opaque-langfuse-trace-id",
            "name": f"request {CANARIES[8]}",
            "input": {
                "tool": "unregistered-dynamic-tool",
                "toolName": CANARIES[8],
                "arguments": {
                    "NIN": CANARIES[1],
                    "beneficiaryName": CANARIES[4],
                    "amount_minor": 2500,
                    "currency": "NGN",
                },
                "context": CANARIES[7],
                "prompt": CANARIES[7],
            },
            "output": {
                "message": CANARIES[7],
                "accountNumber": CANARIES[2],
                "status": "pending",
            },
            "metadata": {"tags": [CANARIES[7]], "retry_count": 0},
            "tags": [CANARIES[7]],
        }
    ]

    cases = from_langfuse(traces)
    assert len(cases) == 1
    assert cases[0].metadata["redaction_policy_version"] == REDACTION_POLICY_VERSION
    _assert_no_canaries(cases)
    assert cases[0].expected_output["status"] == "pending"
    assert "context" not in cases[0].input
    assert "prompt" not in cases[0].input

    class CapturingClient:
        def __init__(self):
            self.items = []

        def create_dataset_item(self, **kwargs):
            self.items.append(kwargs)

    client = CapturingClient()
    assert publish_to_langfuse(cases, client, "privacy-canary-test") == 1
    _assert_no_canaries(client.items)

    jsonl_path = write_eval_cases(cases, tmp_path / "langfuse.jsonl")
    _assert_no_canaries(jsonl_path.read_text(encoding="utf-8"))
