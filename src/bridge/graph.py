"""LangGraph-shaped wiring. Default path is the sync pipeline in nodes.py.

Node sequence:
    policy_gate → planner → worker → verifier → budget → (execute?) → END

`govern_check` is inlined as BudgetGuardian + PolicyGate. Optional compile
when `langgraph` is installed; otherwise `compile_graph()` returns None and
callers use `run_quote_goal`.
"""
from __future__ import annotations

from typing import Any, Callable


def node_sequence() -> list[str]:
    return ["policy_gate", "planner", "worker", "verifier", "budget"]


def compile_graph() -> Callable[..., Any] | None:
    try:
        from langgraph.graph import END, StateGraph  # type: ignore
    except ImportError:
        return None

    from src.bridge.nodes import run_quote_goal
    from src.bridge.state import BridgeState

    graph = StateGraph(BridgeState)
    graph.add_node("run", lambda s: run_quote_goal(s.goal, s.profile))
    graph.set_entry_point("run")
    graph.add_edge("run", END)
    return graph.compile()
