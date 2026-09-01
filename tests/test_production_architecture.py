import json

from agentbridge.core.budget_guardian import BudgetGuardian, payment_required_body
from agentbridge.core.checkpointing import CheckpointConfigurationError, checkpoint_config, checkpoint_payload
from agentbridge.core.graph import compile_graph
from agentbridge.core.hitl import ConfirmationEvidence
from agentbridge.core.rail_switch import RailRouter, RailUnavailableError
from agentbridge.core.router import AgentRouter
from agentbridge.core.state import AgentState, ContextProfile
from agentbridge.tools.payment_engine import UnifiedPaymentEngine
from agentbridge.tools.payment_mcp import PROCESS_PAYMENT
from evals.trace_hydrator import from_langfuse, from_otel, publish_to_langfuse


def test_rail_router_uses_context_and_health_failover():
    profile = ContextProfile(locale="en-KE", currency="KES", payment_rails=["mpesa", "mobile_money"])
    selection = RailRouter().select(profile, currency="KES", availability={"mpesa": False})
    assert selection.rail == "mobile_money"
    assert selection.destination_country == "KE"


def test_rail_router_fails_closed_without_provider():
    profile = ContextProfile(country="GH", currency="GHS", payment_rails=["mtn_momo"])
    try:
        RailRouter().select(profile, currency="GHS", availability={"mtn_momo": False})
        raise AssertionError("expected no-provider hard stop")
    except RailUnavailableError:
        pass


def test_unified_engine_keeps_provider_out_of_tool_interface():
    profile = ContextProfile(country="NG", payment_rails=["paystack", "bank"])
    selection, envelope = UnifiedPaymentEngine().quote(
        profile,
        amount=100,
        currency="NGN",
        destination_country="NG",
    )
    assert selection.rail == "paystack"
    assert envelope.ok is True
    assert "provider" not in PROCESS_PAYMENT.inputSchema["required"]


def test_every_destructive_tool_requires_confirmation():
    state = AgentState()
    router = AgentRouter(confirmation_validator=lambda evidence, state, tool: evidence.subject == "user-1")
    arguments = {
        "amount": 10,
        "currency": "NGN",
        "destination_country": "NG",
        "recipient": "customer-ref",
        "idempotency_key": "idem-12345",
    }
    pending = router.gate(state, PROCESS_PAYMENT, arguments)
    assert pending.decision == "hitl"

    evidence = ConfirmationEvidence(method="oauth", reference="consent-123", subject="user-1")
    unverified = AgentRouter().gate(state, PROCESS_PAYMENT, arguments, confirmation=evidence)
    assert unverified.decision == "block"
    approved = router.gate(state, PROCESS_PAYMENT, arguments, confirmation=evidence)
    assert approved.decision == "dispatch"
    assert state.confirmations[-1]["reference"] == "consent-123"


def test_checkpoint_payload_preserves_complete_state():
    state = AgentState(goal="wait for callback", llm_cost_usd=0.01, processing_fee_usd=0.02)
    payload = checkpoint_payload(state)
    assert set(payload) == set(AgentState.model_fields)
    assert checkpoint_config(state.run_id)["configurable"]["thread_id"] == state.run_id
    assert compile_graph(require_persistence=False) is None
    try:
        compile_graph(require_persistence=True)
        raise AssertionError("production graph must require PostgresSaver")
    except RuntimeError:
        pass


def test_budget_combines_llm_and_processing_costs():
    state = AgentState(profile=ContextProfile(max_run_cost_usd=0.02))
    guardian = BudgetGuardian(emit_oscal=False)
    guardian.charge(state, 0.01, category="llm")
    guardian.charge(state, 0.011, category="processing")
    body = payment_required_body(state)
    assert state.http_status == 402
    assert body["llm_cost_usd"] == 0.01
    assert body["processing_fee_usd"] == 0.011
    assert body["spent_usd"] == 0.021


def test_trace_hydration_redacts_pii_and_scores_retries():
    otel = {
        "spans": [
            {
                "trace_id": "trace-1",
                "span_id": "span-1",
                "name": "process_payment",
                "attributes": {
                    "tool.name": "process_payment",
                    "tool.arguments": json.dumps(
                        {
                            "amount": 20,
                            "currency": "NGN",
                            "destination_country": "NG",
                            "recipient": "+2348000000000",
                            "idempotency_key": "idem-12345",
                        }
                    ),
                    "oauth_scopes": ["payments:execute"],
                    "payment.retry_count": 4,
                    "payment.max_retries": 3,
                },
            }
        ]
    }
    case = from_otel(otel)[0]
    assert case.input["arguments"]["recipient"].startswith("[redacted:")
    assert case.metadata["score"]["failure_retry_behavior"] == 0.0
    assert case.expected_output["pass"] is False


def test_langfuse_hydration_can_publish_linked_dataset_item():
    cases = from_langfuse(
        [
            {
                "id": "lf-1",
                "name": "check_transaction",
                "input": {"tool": "check_transaction", "arguments": {"transaction_id": "txn-1"}},
                "output": {"status": "settled"},
            }
        ]
    )

    class FakeClient:
        def __init__(self):
            self.items = []

        def create_dataset_item(self, **kwargs):
            self.items.append(kwargs)

    client = FakeClient()
    assert publish_to_langfuse(cases, client, "payments-production") == 1
    assert client.items[0]["source_trace_id"] == "lf-1"
