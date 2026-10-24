from agentbridge.core.budget_guardian import (
    HTTP_402_PAYMENT_REQUIRED,
    BudgetGuardian,
    PaymentRequiredStop,
    payment_required_body,
)
from agentbridge.core.state import AgentState, ContextProfile


def test_charge_within_cap():
    state = AgentState(profile=ContextProfile(max_run_cost_usd=0.15))
    g = BudgetGuardian(emit_oscal=False)
    state = g.charge(state, 0.01)
    assert state.status == "running"
    assert state.http_status is None
    assert state.spent_usd == 0.01


def test_hard_stop_sets_http_402():
    state = AgentState(profile=ContextProfile(max_run_cost_usd=0.0015))
    g = BudgetGuardian(emit_oscal=False)
    state = g.charge(state, 0.001)
    state = g.charge(state, 0.001)
    assert state.status == "budget_exceeded"
    assert state.http_status == HTTP_402_PAYMENT_REQUIRED
    assert "spent_usd=" in (state.stop_reason or "")


def test_raise_on_cap():
    state = AgentState(profile=ContextProfile(max_run_cost_usd=0.0001))
    g = BudgetGuardian(emit_oscal=False)
    try:
        g.charge(state, 0.01, raise_on_cap=True)
        raise AssertionError("expected PaymentRequiredStop")
    except PaymentRequiredStop as exc:
        assert exc.status_code == 402
        assert exc.state.http_status == 402


def test_intercept_blocks_further_tools():
    state = AgentState(profile=ContextProfile(max_run_cost_usd=0.001), spent_usd=0.002)
    g = BudgetGuardian(emit_oscal=False)
    state = g.intercept(state)
    assert state.status == "budget_exceeded"
    assert state.http_status == 402


def test_partial_oscal_on_cap(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state = AgentState(profile=ContextProfile(max_run_cost_usd=0.0001), run_id="run402")
    g = BudgetGuardian(emit_oscal=True)
    state = g.charge(state, 0.01)
    assert state.partial_artifacts
    ar = tmp_path / ".venturalitica" / "runs" / "run402" / "assessment-results.oscal.json"
    poam = tmp_path / ".venturalitica" / "runs" / "run402" / "poam.oscal.json"
    assert ar.is_file()
    assert poam.is_file()
    body = payment_required_body(state)
    assert body["status"] == 402
    assert body["error"] == "payment_required"


def test_already_tripped_is_idempotent():
    state = AgentState(
        profile=ContextProfile(max_run_cost_usd=0.001),
        spent_usd=0.05,
        status="budget_exceeded",
        http_status=402,
    )
    g = BudgetGuardian(emit_oscal=False)
    again = g.charge(state, 1.0)
    assert again.spent_usd == 0.05
    assert again.http_status == 402


def test_invalid_cost_cannot_bypass_ceiling():
    state = AgentState(profile=ContextProfile(max_run_cost_usd=1))
    guardian = BudgetGuardian(emit_oscal=False)
    for invalid in (-0.01, float("nan"), float("inf")):
        try:
            guardian.charge(state, invalid)
            raise AssertionError("invalid cost must fail closed")
        except ValueError:
            pass
    assert state.spent_usd == 0
