"""Deprecated compatibility import for ``agentbridge.tools.payment_adapter``."""

from agentbridge.tools.payment_adapter import (  # noqa: F401
    Rail,
    ToolEnvelope,
    clear_fault,
    execute,
    quote,
    set_fault,
    status,
)

__all__ = ["Rail", "ToolEnvelope", "clear_fault", "execute", "quote", "set_fault", "status"]
