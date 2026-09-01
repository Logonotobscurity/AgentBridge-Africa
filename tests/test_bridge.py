from agentbridge.core.graph import node_sequence
from agentbridge.core.orchestrator import run_budget_exhaustion, run_quote_goal
from agentbridge.core.policy import decide
from agentbridge.core.state import ContextProfile
from agentbridge.tools.payment_adapter import execute, quote


def test_quote_ok():
    env = quote("bank", 1000, "NGN", country="NG")
    assert env.ok and env.data["quote_id"]
    assert env.read_only_hint is True
    assert env.destructive_hint is False


def test_execute_requires_idempotency():
    env = execute("bank", "qid", "")
    assert env.ok is False
    assert env.error_code == "missing_idempotency_key"
    assert env.destructive_hint is True


def test_happy_ng():
    p = ContextProfile(currency="NGN", payment_rails=["bank"])
    s = run_quote_goal("quote", p)
    assert s.status == "success"
    assert s.checkpoint.get("last_quote_id")


def test_happy_ke():
    p = ContextProfile(currency="KES", payment_rails=["mpesa"], locale="en-KE")
    s = run_quote_goal("Quote KES via M-Pesa rail", p)
    assert s.status == "success"


def test_offline_fails():
    p = ContextProfile(connectivity="offline_first")
    s = run_quote_goal("quote", p)
    assert s.status == "failed"
    assert s.stop_reason and "offline_first" in s.stop_reason


def test_budget():
    s = run_budget_exhaustion()
    assert s.status == "budget_exceeded"


def test_timeout_fault():
    p = ContextProfile(currency="NGN", payment_rails=["bank"])
    s = run_quote_goal("quote", p, inject_fault="tool_timeout")
    assert s.status == "failed"
    assert s.stop_reason == "tool_error: tool_timeout"


def test_policy_blocks_offline_execute():
    p = ContextProfile(connectivity="offline_first")
    decision, _ = decide("execute", p, has_idempotency=True, destructive=True)
    assert decision == "block"


def test_policy_escalates_intermittent_execute():
    p = ContextProfile(connectivity="intermittent")
    decision, _ = decide("execute", p, has_idempotency=True, destructive=True)
    assert decision == "escalate"


def test_orchestrator_honors_policy_escalation():
    profile = ContextProfile(connectivity="intermittent", hitl_amount_threshold=100_000)
    state = run_quote_goal("pay", profile, amount=100, execute_payment=True)
    assert state.status == "awaiting_hitl"
    assert state.hitl_pending is not None


def test_node_sequence():
    assert node_sequence()[0] == "policy_gate"


def test_payment_limit_fails_before_provider_and_emits_poam(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    profile = ContextProfile(aml_daily_limit=100)
    state = run_quote_goal("oversized transfer", profile, amount=101)
    assert state.status == "failed"
    assert state.stop_reason and "payment_limit_exceeded" in state.stop_reason
    assert len(state.steps) == 0
    assert (tmp_path / ".venturalitica" / "runs" / state.run_id / "poam.oscal.json").exists()


def test_legacy_imports_delegate_to_canonical_runtime():
    from src.bridge.nodes import run_quote_goal as legacy_run
    from tools.payments_adapter import quote as legacy_quote

    assert legacy_run is run_quote_goal
    assert legacy_quote is quote
