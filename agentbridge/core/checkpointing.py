"""Production LangGraph persistence backed exclusively by encrypted PostgreSQL.

All graph channel values and non-empty pending sends are forced through an
authenticated encrypted serializer. Indexing fields remain opaque and bounded.
Legacy plaintext checkpoint payloads are rejected instead of silently resumed.
"""

from __future__ import annotations

import base64
import binascii
import os
import re
from collections.abc import Mapping
from contextlib import asynccontextmanager, contextmanager
from typing import Any, AsyncIterator, Iterator

from agentbridge.core.state import AgentState

POSTGRES_DSN_ENV = "AGENTBRIDGE_POSTGRES_DSN"
CHECKPOINT_KEY_ENV = "AGENTBRIDGE_CHECKPOINT_ENCRYPTION_KEY_B64"
CHECKPOINT_KEY_BYTES = 32

_PRIMITIVE_MARKER = "__agentbridge_encrypted_primitive_channel_v1__"
_PENDING_SENDS_MARKER = "__agentbridge_encrypted_pending_sends_v1__"
_ID_PATTERN = re.compile(
    r"^(?:[0-9a-fA-F]{32}|"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|"
    r"[0-9A-HJKMNP-TV-Z]{26})$"
)
_NAMESPACE_PATTERN = re.compile(r"^[A-Za-z0-9_.:/|-]{0,128}$")
_ALLOWED_SOURCES = frozenset({"input", "loop", "update", "resume", "fork"})


class CheckpointConfigurationError(RuntimeError):
    pass


class _StrictEncryptedCheckpointSerializer:
    """Reject plaintext type tags instead of EncryptedSerializer's fallback path."""

    def __init__(self, encrypted: Any) -> None:
        self._encrypted = encrypted

    def dumps_typed(self, value: Any) -> tuple[str, bytes]:
        payload = self._encrypted.dumps_typed(value)
        if not isinstance(payload, tuple) or len(payload) != 2 or not payload[0].endswith("+aes"):
            raise ValueError("checkpoint serializer did not produce an AES-encrypted payload")
        return payload

    def loads_typed(self, payload: tuple[str, bytes]) -> Any:
        if (
            not isinstance(payload, tuple)
            or len(payload) != 2
            or not isinstance(payload[0], str)
            or not payload[0].endswith("+aes")
        ):
            raise ValueError("refusing unencrypted or unsupported checkpoint payload")
        return self._encrypted.loads_typed(payload)


def _is_opaque_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_ID_PATTERN.fullmatch(value))


def checkpoint_config(run_id: str) -> dict[str, dict[str, str]]:
    if not _is_opaque_id(run_id):
        raise ValueError("run_id must be an opaque UUID/ULID; do not use customer identifiers")
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
    """Build authenticated AES-EAX serialization from a raw 256-bit key.

    The key is Base64 transport for 32 cryptographically random bytes. It must be
    provisioned by a secret manager; printable passphrases are not accepted.
    The wrapper rejects legacy plaintext type tags returned by the upstream
    EncryptedSerializer compatibility fallback.
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
        encrypted = EncryptedSerializer.from_pycryptodome_aes(key=key)
        return _StrictEncryptedCheckpointSerializer(encrypted)
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise CheckpointConfigurationError(
            "PyCryptodome is required for encrypted checkpoints; install the postgres extra"
        ) from exc


def _safe_checkpoint_config(config: Mapping[str, Any]) -> dict[str, Any]:
    configurable = config.get("configurable")
    if not isinstance(configurable, Mapping):
        raise CheckpointConfigurationError("checkpoint config requires a configurable mapping")
    thread_id = configurable.get("thread_id")
    if not _is_opaque_id(thread_id):
        raise CheckpointConfigurationError("checkpoint thread_id must be an opaque UUID/ULID")
    namespace = configurable.get("checkpoint_ns", "")
    if not isinstance(namespace, str) or not _NAMESPACE_PATTERN.fullmatch(namespace):
        raise CheckpointConfigurationError("checkpoint namespace contains unsupported characters")

    safe_configurable = {
        "thread_id": thread_id,
        "checkpoint_ns": namespace,
    }
    checkpoint_id = configurable.get("checkpoint_id")
    if checkpoint_id is not None:
        if not _is_opaque_id(checkpoint_id):
            raise CheckpointConfigurationError("checkpoint_id must be an opaque UUID/ULID")
        safe_configurable["checkpoint_id"] = checkpoint_id
    # Drop caller metadata/configurable extensions; they are not required for
    # saver indexing and must not become plaintext JSONB metadata.
    return {"configurable": safe_configurable}


def _safe_checkpoint_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Retain only the small metadata contract needed for checkpoint operations."""
    if not isinstance(metadata, Mapping):
        return {}
    result: dict[str, Any] = {}
    source = metadata.get("source")
    if isinstance(source, str) and source in _ALLOWED_SOURCES:
        result["source"] = source
    step = metadata.get("step")
    if type(step) is int and -1 <= step <= 1_000_000_000:
        result["step"] = step
    parents = metadata.get("parents")
    if isinstance(parents, Mapping):
        safe_parents = {
            str(namespace): checkpoint_id
            for namespace, checkpoint_id in parents.items()
            if isinstance(namespace, str)
            and _NAMESPACE_PATTERN.fullmatch(namespace)
            and _is_opaque_id(checkpoint_id)
        }
        if safe_parents:
            result["parents"] = safe_parents
    return result


def _prepare_checkpoint(checkpoint: Mapping[str, Any], serde: Any) -> dict[str, Any]:
    """Force primitives and pending sends into encrypted blob storage."""
    prepared = dict(checkpoint)
    channel_values = dict(prepared.get("channel_values") or {})
    for key, value in tuple(channel_values.items()):
        if value is None or type(value) in (str, int, float, bool):
            channel_values[key] = (_PRIMITIVE_MARKER, value)
    prepared["channel_values"] = channel_values

    pending_sends = prepared.get("pending_sends")
    if pending_sends is not None and pending_sends != []:
        type_tag, ciphertext = serde.dumps_typed(pending_sends)
        if not type_tag.endswith("+aes"):
            raise CheckpointConfigurationError("pending sends were not encrypted")
        prepared["pending_sends"] = {
            _PENDING_SENDS_MARKER: True,
            "type": type_tag,
            "payload_b64": base64.b64encode(ciphertext).decode("ascii"),
        }
    else:
        # Empty sends contain no run data and remain a safe empty sentinel.
        prepared["pending_sends"] = []
    return prepared


def _restore_checkpoint(checkpoint_tuple: Any, serde: Any) -> Any:
    """Restore encrypted channel values and pending sends after base saver loading."""
    checkpoint = dict(checkpoint_tuple.checkpoint)
    channel_values = dict(checkpoint.get("channel_values") or {})
    for key, value in tuple(channel_values.items()):
        if isinstance(value, (tuple, list)) and len(value) == 2 and value[0] == _PRIMITIVE_MARKER:
            channel_values[key] = value[1]
        elif value is None or type(value) in (str, int, float, bool):
            raise ValueError("refusing legacy plaintext primitive channel value")
    checkpoint["channel_values"] = channel_values

    pending_sends = checkpoint.get("pending_sends", [])
    if pending_sends == [] or pending_sends is None:
        checkpoint["pending_sends"] = []
    elif isinstance(pending_sends, Mapping) and pending_sends.get(_PENDING_SENDS_MARKER) is True:
        type_tag = pending_sends.get("type")
        encoded = pending_sends.get("payload_b64")
        if not isinstance(type_tag, str) or not type_tag.endswith("+aes") or not isinstance(encoded, str):
            raise ValueError("invalid encrypted pending-sends envelope")
        try:
            ciphertext = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("invalid pending-sends ciphertext encoding") from exc
        checkpoint["pending_sends"] = serde.loads_typed((type_tag, ciphertext))
    else:
        raise ValueError("refusing legacy plaintext pending-sends payload")

    metadata = _safe_checkpoint_metadata(checkpoint_tuple.metadata)
    return checkpoint_tuple._replace(checkpoint=checkpoint, metadata=metadata)


class _EncryptedPostgresSaverMixin:
    def put(self, config: Mapping[str, Any], checkpoint: Mapping[str, Any], metadata: Mapping[str, Any], new_versions: Mapping[str, Any]) -> Any:
        safe_config = _safe_checkpoint_config(config)
        checkpoint_id = checkpoint.get("id")
        if not _is_opaque_id(checkpoint_id):
            raise CheckpointConfigurationError("checkpoint id must be an opaque UUID/ULID")
        return super().put(
            safe_config,
            _prepare_checkpoint(checkpoint, self.serde),
            _safe_checkpoint_metadata(metadata),
            new_versions,
        )

    def _load_checkpoint_tuple(self, value: Any) -> Any:
        thread_id = value.get("thread_id") if isinstance(value, Mapping) else None
        if not _is_opaque_id(thread_id):
            raise ValueError("refusing checkpoint with non-opaque thread_id")
        return _restore_checkpoint(super()._load_checkpoint_tuple(value), self.serde)


class _EncryptedAsyncPostgresSaverMixin:
    async def aput(self, config: Mapping[str, Any], checkpoint: Mapping[str, Any], metadata: Mapping[str, Any], new_versions: Mapping[str, Any]) -> Any:
        safe_config = _safe_checkpoint_config(config)
        checkpoint_id = checkpoint.get("id")
        if not _is_opaque_id(checkpoint_id):
            raise CheckpointConfigurationError("checkpoint id must be an opaque UUID/ULID")
        return await super().aput(
            safe_config,
            _prepare_checkpoint(checkpoint, self.serde),
            _safe_checkpoint_metadata(metadata),
            new_versions,
        )

    async def _load_checkpoint_tuple(self, value: Any) -> Any:
        thread_id = value.get("thread_id") if isinstance(value, Mapping) else None
        if not _is_opaque_id(thread_id):
            raise ValueError("refusing checkpoint with non-opaque thread_id")
        loaded = await super()._load_checkpoint_tuple(value)
        return _restore_checkpoint(loaded, self.serde)


@contextmanager
def postgres_saver(
    dsn: str | None = None,
    *,
    setup: bool = False,
) -> Iterator[Any]:
    """Yield a PostgresSaver with encrypted channels, pending sends and writes."""
    try:
        from psycopg import Connection
        from psycopg.rows import dict_row
        from langgraph.checkpoint.postgres import PostgresSaver
    except ImportError as exc:  # pragma: no cover - production extra
        raise CheckpointConfigurationError(
            "install agentbridge-africa[postgres] for PostgreSQL checkpoint support"
        ) from exc

    serde = _checkpoint_serde()

    class EncryptedPostgresSaver(_EncryptedPostgresSaverMixin, PostgresSaver):
        pass

    with Connection.connect(
        _dsn(dsn), autocommit=True, prepare_threshold=0, row_factory=dict_row
    ) as conn:
        saver = EncryptedPostgresSaver(conn, serde=serde)
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

    class EncryptedAsyncPostgresSaver(_EncryptedAsyncPostgresSaverMixin, AsyncPostgresSaver):
        pass

    async with await AsyncConnection.connect(
        _dsn(dsn), autocommit=True, prepare_threshold=0, row_factory=dict_row
    ) as conn:
        saver = EncryptedAsyncPostgresSaver(conn, serde=serde)
        if setup:
            await saver.setup()
        yield saver
