"""Production LangGraph persistence backed exclusively by encrypted PostgreSQL.

SQLite is intentionally not offered here. Tests may inject an in-memory saver,
but deployed runtimes must supply PostgreSQL and a 32-byte checkpoint key.
Checkpoint serialization is authenticated-encrypted before it reaches the saver.
"""

from __future__ import annotations

import base64
import binascii
import os
from contextlib import asynccontextmanager, contextmanager
from typing import Any, AsyncIterator, Iterator

from agentbridge.core.state import AgentState

POSTGRES_DSN_ENV = "AGENTBRIDGE_POSTGRES_DSN"
CHECKPOINT_KEY_ENV = "AGENTBRIDGE_CHECKPOINT_ENCRYPTION_KEY_B64"
CHECKPOINT_KEY_BYTES = 32


class CheckpointConfigurationError(RuntimeError):
    pass


def checkpoint_config(run_id: str) -> dict[str, dict[str, str]]:
    if not run_id:
        raise ValueError("run_id is required for checkpoint isolation")
    return {"configurable": {"thread_id": run_id, "checkpoint_ns": "payments"}}


def checkpoint_payload(state: AgentState) -> dict[str, Any]:
    """Return full resume state for tests/inspection; never persist this dict directly.

    Production persistence must go through the encrypted saver factories below.
    The unwrapped state includes business context, tool results and fallback data.
    """
    return state.model_dump(mode="json", exclude_none=False)


def _dsn(value: str | None) -> str:
    dsn = value or os.getenv(POSTGRES_DSN_ENV, "")
    if not dsn.startswith(("postgresql://", "postgresql+psycopg://")):
        raise CheckpointConfigurationError(
            f"{POSTGRES_DSN_ENV} must be a PostgreSQL connection string"
        )
    return dsn


def _checkpoint_serde() -> Any:
    """Build authenticated AES-EAX checkpoint serialization from a raw 256-bit key.

    The key is Base64 transport for 32 cryptographically random bytes. It must be
    provisioned by a secret manager; printable passphrases are not accepted.
    """
    encoded = os.getenv(CHECKPOINT_KEY_ENV, "")
    if not encoded:
        raise CheckpointConfigurationError(
            f"{CHECKPOINT_KEY_ENV} must be provisioned; refusing plaintext checkpoints"
        )
    try:
        key = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise CheckpointConfigurationError(
            f"{CHECKPOINT_KEY_ENV} must contain valid Base64"
        ) from exc
    if len(key) != CHECKPOINT_KEY_BYTES:
        raise CheckpointConfigurationError(
            f"{CHECKPOINT_KEY_ENV} must decode to exactly {CHECKPOINT_KEY_BYTES} bytes"
        )

    try:
        from langgraph.checkpoint.serde.encrypted import EncryptedSerializer
    except ImportError as exc:  # pragma: no cover - production extra
        raise CheckpointConfigurationError(
            "install agentbridge-africa[postgres] with PyCryptodome for encrypted checkpoints"
        ) from exc

    try:
        return EncryptedSerializer.from_pycryptodome_aes(key=key)
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise CheckpointConfigurationError(
            "PyCryptodome is required for encrypted checkpoints; install the postgres extra"
        ) from exc


@contextmanager
def postgres_saver(
    dsn: str | None = None,
    *,
    setup: bool = False,
) -> Iterator[Any]:
    """Yield a PostgresSaver whose checkpoint and pending-write payloads are encrypted."""
    try:
        from psycopg import Connection
        from psycopg.rows import dict_row
        from langgraph.checkpoint.postgres import PostgresSaver
    except ImportError as exc:  # pragma: no cover - production extra
        raise CheckpointConfigurationError(
            "install agentbridge-africa[postgres] for PostgreSQL checkpoint support"
        ) from exc

    serde = _checkpoint_serde()
    with Connection.connect(
        _dsn(dsn), autocommit=True, prepare_threshold=0, row_factory=dict_row
    ) as conn:
        saver = PostgresSaver(conn, serde=serde)
        if setup:
            saver.setup()
        yield saver


@asynccontextmanager
async def async_postgres_saver(
    dsn: str | None = None,
    *,
    setup: bool = False,
) -> AsyncIterator[Any]:
    """Yield an AsyncPostgresSaver with the same mandatory encryption policy."""
    try:
        from psycopg import AsyncConnection
        from psycopg.rows import dict_row
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    except ImportError as exc:  # pragma: no cover - production extra
        raise CheckpointConfigurationError(
            "install agentbridge-africa[postgres] for asynchronous PostgreSQL checkpoint support"
        ) from exc

    serde = _checkpoint_serde()
    async with await AsyncConnection.connect(
        _dsn(dsn), autocommit=True, prepare_threshold=0, row_factory=dict_row
    ) as conn:
        saver = AsyncPostgresSaver(conn, serde=serde)
        if setup:
            await saver.setup()
        yield saver
