"""LangGraph-shaped wiring. Default path is the canonical sync orchestrator.

Node sequence:
    policy_gate → planner → worker → hitl → verifier → budget → compliance → END

``govern_check`` is inlined as BudgetGuardian + PolicyGate + HITL. Optional
compile when ``langgraph`` is installed; otherwise ``compile_graph()`` returns
None and callers use ``run_quote_goal``.
"""

from __future__ import annotations

from typing import Any, Callable


def node_sequence() -> list[str]:
    return ["policy_gate", "planner", "worker", "hitl", "verifier", "budget", "compliance"]


def compile_payment_graph(
    nodes: dict[str, Callable[[Any], Any]],
    *,
    checkpointer: Any,
) -> Callable[..., Any]:
    """Compile the full payment lifecycle with a checkpoint after every node."""
    if checkpointer is None:
        raise RuntimeError("payment graph requires PostgresSaver")
    required = node_sequence()
    missing = [name for name in required if name not in nodes]
    if missing:
        raise ValueError(f"missing payment graph nodes: {', '.join(missing)}")
    try:
        from langgraph.graph import END, StateGraph  # type: ignore
    except ImportError as exc:  # pragma: no cover - production extra
        raise RuntimeError("install agentbridge-africa[postgres] for LangGraph") from exc

    from agentbridge.core.state import AgentState

    graph = StateGraph(AgentState)
    for name in required:
        graph.add_node(name, nodes[name])
    graph.set_entry_point(required[0])
    for current, following in zip(required, required[1:]):
        graph.add_edge(current, following)
    graph.add_edge(required[-1], END)
    return graph.compile(checkpointer=checkpointer)


def compile_graph(
    *,
    checkpointer: Any | None = None,
    require_persistence: bool = False,
) -> Callable[..., Any] | None:
    """Compile with an injected PostgresSaver in production.

    ``require_persistence`` prevents a deployment from silently running an
    asynchronous payment graph without durable checkpoints.
    """
    if require_persistence and checkpointer is None:
        raise RuntimeError("production graph requires PostgresSaver")
    try:
        from langgraph.graph import END, StateGraph  # type: ignore
    except ImportError:
        return None

    from agentbridge.core.orchestrator import run_quote_goal
    from agentbridge.core.state import BridgeState

    graph = StateGraph(BridgeState)
    graph.add_node("run", lambda s: run_quote_goal(s.goal, s.profile))
    graph.set_entry_point("run")
    graph.add_edge("run", END)
    return graph.compile(checkpointer=checkpointer)
