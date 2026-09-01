"""AgentBridge run state — versioned public API (v2).

Re-exports the hardened ``AgentState`` so existing imports keep working.
New fields are optional with defaults; v1 checkpoints still deserialize.
"""

from agentbridge.core.state import (  # noqa: F401
    AGENT_STATE_SCHEMA_VERSION,
    AgentState,
    BridgeState,
    ContextProfile,
    HitlPending,
    SpanRecord,
    StepResult,
)
