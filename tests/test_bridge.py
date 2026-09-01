from src.bridge.graph import node_sequence
from src.bridge.nodes import run_budget_exhaustion, run_quote_goal
from src.bridge.policy_gate import decide
from src.bridge.state import ContextProfile
from tools.payments_adapter import execute, quote


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


def test_node_sequence():
    assert node_sequence()[0] == "policy_gate"
