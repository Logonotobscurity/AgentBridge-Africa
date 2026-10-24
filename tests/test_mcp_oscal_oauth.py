import json
from pathlib import Path

from agentbridge.compliance.oscal_exporter import Finding, export_oscal_results
from agentbridge.core.circuit_breaker import CircuitBreaker, CircuitOpenError
from agentbridge.core.hitl import HitlGate
from agentbridge.core.oauth import OAuth21Provider, OAuthError, scope_allows
from agentbridge.core.router import AgentRouter
from agentbridge.core.state import AGENT_STATE_SCHEMA_VERSION, AgentState
from agentbridge.tools.mcp_types import annotations_dict
from agentbridge.tools.payment_mcp import (
    MPESA_QUERY_STATUS,
    MPESA_STK_PUSH,
    PAYMENT_TOOLS,
    QUOTE_PAYMENT,
)
from evals.production_sampler import score_trace


def test_tool_annotations_split_read_vs_destructive():
    push = annotations_dict(MPESA_STK_PUSH)
    query = annotations_dict(MPESA_QUERY_STATUS)
    assert push["readOnly"] is False and push["destructive"] is True and push["idempotent"] is False
    assert push["readOnlyHint"] is False and push["destructiveHint"] is True
    assert query["readOnly"] is True and query["destructive"] is False and query["idempotent"] is True
    assert all(annotations_dict(t) for t in PAYMENT_TOOLS)


def test_server_card_matches_native_tool_contracts():
    card = json.loads((Path(__file__).parents[1] / ".well-known" / "mcp.json").read_text())
    discovered = {tool["name"]: tool for tool in card["tools"]}
    assert set(discovered) == {tool.name for tool in PAYMENT_TOOLS}
    for tool in PAYMENT_TOOLS:
        entry = discovered[tool.name]
        annotations = annotations_dict(tool)
        assert entry["inputSchema"] == tool.inputSchema
        for name in ("readOnly", "destructive", "idempotent", "readOnlyHint", "destructiveHint", "idempotentHint"):
            assert entry["annotations"][name] == annotations[name]


def test_oscal_poam_on_failed_budget(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    findings = [
        Finding(control_id="AB-BUDGET-1", title="Budget", success=False, rationale="cap blown", related_task="Raise ceiling"),
        Finding(control_id="AB-AML-1", title="AML daily limit", success=True, rationale="under limit"),
    ]
    paths = export_oscal_results("r1", findings)
    assert "assessment-results" in paths and "poam" in paths
    import json

    ar = json.loads((tmp_path / paths["assessment-results"]).read_text())
    assert ar["assessment-results"]["metadata"]["oscal-version"] == "1.2.1"
    states = [f["target"]["status"]["state"] for f in ar["assessment-results"]["results"][0]["findings"]]
    assert "not-satisfied" in states and "satisfied" in states
    poam = json.loads((tmp_path / paths["poam"]).read_text())
    assert poam["plan-of-action-and-milestones"]["poam-items"][0]["related-findings"]


def test_pkce_roundtrip_and_scope_binding():
    oauth = OAuth21Provider(allowed_redirects={"http://127.0.0.1/cb"})
    verifier, challenge = oauth.generate_pkce()
    code = oauth.authorize(
        client_id="ledger-bot",
        redirect_uri="http://127.0.0.1/cb",
        code_challenge=challenge,
        scopes=["payments:quote", "payments:status"],
    )
    token = oauth.exchange(
        code=code,
        code_verifier=verifier,
        redirect_uri="http://127.0.0.1/cb",
        client_id="ledger-bot",
    )
    claims = oauth.introspect(token)
    assert scope_allows(claims, "payments:quote")
    assert not scope_allows(claims, "payments:execute")
    try:
        oauth.exchange(code=code, code_verifier=verifier, redirect_uri="http://127.0.0.1/cb", client_id="ledger-bot")
        raise AssertionError("authorization codes must be single-use")
    except OAuthError:
        pass


def test_pkce_rejects_wrong_verifier_and_open_redirect():
    oauth = OAuth21Provider(allowed_redirects={"https://app.example/cb"})
    verifier, challenge = oauth.generate_pkce()
    try:
        oauth.authorize(client_id="x", redirect_uri="https://evil.example/cb", code_challenge=challenge)
        raise AssertionError("open redirect must fail")
    except OAuthError:
        pass
    code = oauth.authorize(client_id="x", redirect_uri="https://app.example/cb", code_challenge=challenge)
    try:
        oauth.exchange(code=code, code_verifier="not-the-verifier", redirect_uri="https://app.example/cb", client_id="x")
        raise AssertionError("bad pkce must fail")
    except OAuthError:
        pass


def test_hitl_pauses_destructive_over_threshold():
    gate = HitlGate()
    ticket = gate.evaluate(MPESA_STK_PUSH, amount=250_000, currency="NGN", threshold=100_000)
    assert ticket is not None and ticket.status == "pending"
    gate.decide(ticket.ticket_id, "approved", operator="treasury")
    assert gate.tickets[ticket.ticket_id].status == "approved"
    assert gate.evaluate(QUOTE_PAYMENT, amount=250_000, currency="NGN", threshold=100_000) is None


def test_router_blocks_execute_without_scope():
    oauth = OAuth21Provider(allowed_redirects={"http://127.0.0.1/cb"})
    verifier, challenge = oauth.generate_pkce()
    code = oauth.authorize(
        client_id="reader",
        redirect_uri="http://127.0.0.1/cb",
        code_challenge=challenge,
        scopes=["payments:quote"],
    )
    token = oauth.exchange(code=code, code_verifier=verifier, redirect_uri="http://127.0.0.1/cb", client_id="reader")
    claims = oauth.introspect(token)
    router = AgentRouter()
    route = router.gate(AgentState(), MPESA_STK_PUSH, {"amount": 10}, token=claims)
    assert route.decision == "block"


def test_circuit_breaker_opens():
    br = CircuitBreaker(name="mpesa", failure_threshold=2, recovery_timeout_s=60)

    def boom():
        raise RuntimeError("provider_unavailable")

    for _ in range(2):
        try:
            br.call(boom)
        except RuntimeError:
            pass
    assert br.state == "open"
    try:
        br.call(lambda: "ok")
        raise AssertionError("open circuit must fail")
    except CircuitOpenError:
        pass


def test_agent_state_v1_payload_loads():
    payload = {
        "run_id": "abc",
        "goal": "quote",
        "spent_usd": 0.0,
        "status": "running",
    }
    state = AgentState.model_validate(payload)
    assert state.schema_version == AGENT_STATE_SCHEMA_VERSION
    assert state.hitl_pending is None
    assert state.fallback_queue == []
    assert state.oauth_scopes == []


def test_production_sampler_scores_grounding():
    bad = score_trace({"id": "1", "tool": "mpesa_stk_push", "arguments": {"amount": 1}, "oauth_scopes": ["payments:quote"]})
    assert bad["pass"] is False
    good = score_trace(
        {
            "id": "2",
            "tool": "mpesa_stk_query",
            "arguments": {"checkout_request_id": "ws_123"},
            "oauth_scopes": ["payments:status"],
        }
    )
    assert good["pass"] is True


def test_router_budget_interceptor_never_calls_provider():
    state = AgentState(spent_usd=1.0)
    state.profile.max_run_cost_usd = 0.5
    called = False

    def provider():
        nonlocal called
        called = True

    state, response = AgentRouter().dispatch(state, QUOTE_PAYMENT, provider)
    assert called is False
    assert state.status == "budget_exceeded"
    assert response["status"] == 402


def test_router_requires_idempotency_before_destructive_dispatch():
    route = AgentRouter().gate(AgentState(), MPESA_STK_PUSH, {"amount": 10})
    assert route.decision == "block"
    assert "idempotency_key" in route.reason


def test_offline_read_uses_fallback_without_provider_call():
    state = AgentState()
    state.profile.connectivity = "offline_first"
    called = False

    def provider():
        nonlocal called
        called = True

    state, response = AgentRouter().dispatch(state, QUOTE_PAYMENT, provider)
    assert called is False
    assert state.status == "degraded"
    assert response["cached"] is False
    assert state.fallback_queue == []


def test_oscal_accepts_mapping_and_rejects_unsafe_run_id(tmp_path):
    paths = export_oscal_results(
        "mapping-run",
        [{"control_id": "AB-AML-1", "title": "AML", "success": True, "rationale": "passed"}],
        root=tmp_path,
    )
    assert paths["assessment-results"].endswith("assessment-results.oscal.json")
    try:
        export_oscal_results("../escape", [], root=tmp_path)
        raise AssertionError("path traversal must be rejected")
    except ValueError:
        pass


def test_satisfied_reassessment_removes_stale_poam(tmp_path):
    failed = Finding(control_id="AB-BUDGET-1", title="Budget", success=False, rationale="failed")
    export_oscal_results("rerun", [failed], root=tmp_path)
    poam = tmp_path / ".venturalitica" / "runs" / "rerun" / "poam.oscal.json"
    assert poam.exists()
    export_oscal_results(
        "rerun",
        [Finding(control_id="AB-BUDGET-1", title="Budget", success=True, rationale="passed")],
        root=tmp_path,
    )
    assert not poam.exists()
