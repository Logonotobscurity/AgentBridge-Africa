"""Production LangGraph persistence backed exclusively by PostgreSQL.

SQLite is intentionally not offered here. Tests may inject an in-memory saver,
but deployed runtimes must supply ``AGENTBRIDGE_POSTGRES_DSN`` and install the
``postgres`` project extra.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager, contextmanager
from typing import Any, AsyncIterator, Iterator

from agentbridge.core.state import AgentState

POSTGRES_DSN_ENV = "AGENTBRIDGE_POSTGRES_DSN"


class CheckpointConfigurationError(RuntimeError):
    pass


def checkpoint_config(run_id: str) -> dict[str, dict[str, str]]:
    if not run_id:
        raise ValueError("run_id is required for checkpoint isolation")
    return {"configurable": {"thread_id": run_id, "checkpoint_ns": "payments"}}


def checkpoint_payload(state: AgentState) -> dict[str, Any]:
    """Serialize every versioned state field for crash-safe resumption."""
    return state.model_dump(mode="json", exclude_none=False)


def _dsn(value: str | None) -> str:
    dsn = value or os.getenv(POSTGRES_DSN_ENV, "")
    if not dsn.startswith(("postgresql://", "postgresql+psycopg://")):
        raise CheckpointConfigurationError(
            f"{POSTGRES_DSN_ENV} must be a PostgreSQL connection string"
        )
    return dsn


@contextmanager
def postgres_saver(
    dsn: str | None = None,
    *,
    setup: bool = False,
) -> Iterator[Any]:
    """Yield a configured PostgresSaver; run setup only during deployment migrations."""
    try:
        from langgraph.checkpoint.postgres import PostgresSaver
    except ImportError as exc:  # pragma: no cover - production extra
        raise CheckpointConfigurationError(
            "install agentbridge-africa[postgres] for PostgresSaver"
        ) from exc

    with PostgresSaver.from_conn_string(_dsn(dsn)) as saver:
        if setup:
            saver.setup()
        yield saver


@asynccontextmanager
async def async_postgres_saver(
    dsn: str | None = None,
    *,
    setup: bool = False,
) -> AsyncIterator[Any]:
    """Yield AsyncPostgresSaver for callback/webhook-driven payment graphs."""
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    except ImportError as exc:  # pragma: no cover - production extra
        raise CheckpointConfigurationError(
            "install agentbridge-africa[postgres] for AsyncPostgresSaver"
        ) from exc

    async with AsyncPostgresSaver.from_conn_string(_dsn(dsn)) as saver:
        if setup:
            await saver.setup()
        yield saver
