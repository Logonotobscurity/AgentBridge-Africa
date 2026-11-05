import base64
import sys
from types import ModuleType
from collections import namedtuple
import json

import pytest

from agentbridge.core import checkpointing
from agentbridge.core.checkpointing import (
    _StrictEncryptedCheckpointSerializer,
    CHECKPOINT_KEY_ENV,
    CheckpointConfigurationError,
    _checkpoint_serde,
    _prepare_checkpoint,
    _restore_checkpoint,
    _safe_checkpoint_metadata,
    _safe_checkpoint_config,
    checkpoint_config,
)


def _install_fake_encrypted_serializer(monkeypatch, fake_serializer):
    for name in (
        "langgraph",
        "langgraph.checkpoint",
        "langgraph.checkpoint.serde",
    ):
        package = ModuleType(name)
        package.__path__ = []
        monkeypatch.setitem(sys.modules, name, package)

    encrypted = ModuleType("langgraph.checkpoint.serde.encrypted")
    encrypted.EncryptedSerializer = fake_serializer
    monkeypatch.setitem(sys.modules, encrypted.__name__, encrypted)


def test_checkpoint_serializer_fails_closed_without_key(monkeypatch):
    monkeypatch.delenv(CHECKPOINT_KEY_ENV, raising=False)

    with pytest.raises(CheckpointConfigurationError, match="refusing plaintext checkpoints"):
        _checkpoint_serde()


@pytest.mark.parametrize("encoded", ["not-base64!!", ""])
def test_checkpoint_serializer_rejects_invalid_base64(monkeypatch, encoded):
    monkeypatch.setenv(CHECKPOINT_KEY_ENV, encoded)

    with pytest.raises(CheckpointConfigurationError):
        _checkpoint_serde()


@pytest.mark.parametrize("raw_key", [b"x" * 16, b"x" * 24, b"x" * 31, b"x" * 33])
def test_checkpoint_serializer_requires_exactly_32_key_bytes(monkeypatch, raw_key):
    monkeypatch.setenv(CHECKPOINT_KEY_ENV, base64.b64encode(raw_key).decode("ascii"))

    with pytest.raises(CheckpointConfigurationError, match="exactly 32 bytes"):
        _checkpoint_serde()


def test_checkpoint_serializer_uses_raw_32_byte_key(monkeypatch):
    raw_key = bytes(range(32))
    monkeypatch.setenv(CHECKPOINT_KEY_ENV, base64.b64encode(raw_key).decode("ascii"))

    calls = {}

    class FakeEncryptedSerializer:
        @staticmethod
        def from_pycryptodome_aes(*, key):
            calls["key"] = key
            return object()

    _install_fake_encrypted_serializer(monkeypatch, FakeEncryptedSerializer)

    result = _checkpoint_serde()

    assert result is not None
    assert calls["key"] == raw_key
    assert isinstance(calls["key"], bytes)
    assert len(calls["key"]) == 32


def test_checkpoint_serializer_requires_langgraph_crypto_support(monkeypatch):
    raw_key = bytes(range(32))
    monkeypatch.setenv(CHECKPOINT_KEY_ENV, base64.b64encode(raw_key).decode("ascii"))

    # Install package parents but intentionally omit the encrypted module.
    for name in (
        "langgraph",
        "langgraph.checkpoint",
        "langgraph.checkpoint.serde",
    ):
        package = ModuleType(name)
        package.__path__ = []
        monkeypatch.setitem(sys.modules, name, package)
    monkeypatch.delitem(sys.modules, "langgraph.checkpoint.serde.encrypted", raising=False)

    with pytest.raises(CheckpointConfigurationError, match="encrypted checkpoints"):
        _checkpoint_serde()


def test_sync_saver_factory_requires_key_before_connecting(monkeypatch):
    monkeypatch.delenv(CHECKPOINT_KEY_ENV, raising=False)

    class Connection:
        @staticmethod
        def connect(*args, **kwargs):
            raise AssertionError("database connection must not be opened without the key")

    psycopg = ModuleType("psycopg")
    psycopg.__path__ = []
    psycopg.Connection = Connection
    rows = ModuleType("psycopg.rows")
    rows.dict_row = object()

    langgraph = ModuleType("langgraph")
    langgraph.__path__ = []
    checkpoint = ModuleType("langgraph.checkpoint")
    checkpoint.__path__ = []
    postgres = ModuleType("langgraph.checkpoint.postgres")
    postgres.PostgresSaver = object()

    monkeypatch.setitem(sys.modules, "psycopg", psycopg)
    monkeypatch.setitem(sys.modules, "psycopg.rows", rows)
    monkeypatch.setitem(sys.modules, "langgraph", langgraph)
    monkeypatch.setitem(sys.modules, "langgraph.checkpoint", checkpoint)
    monkeypatch.setitem(sys.modules, "langgraph.checkpoint.postgres", postgres)

    with pytest.raises(CheckpointConfigurationError, match="refusing plaintext checkpoints"):
        with checkpointing.postgres_saver("postgresql://example.invalid/db"):
            pass



def test_strict_checkpoint_serializer_rejects_plaintext_type_tags():
    class FakeEncrypted:
        def __init__(self):
            self.loads = []

        def dumps_typed(self, value):
            return ("msgpack+aes", b"ciphertext")

        def loads_typed(self, payload):
            self.loads.append(payload)
            return {"ok": True}

    inner = FakeEncrypted()
    serde = _StrictEncryptedCheckpointSerializer(inner)

    assert serde.dumps_typed({"sensitive": "value"}) == ("msgpack+aes", b"ciphertext")
    with pytest.raises(ValueError, match="refusing unencrypted"):
        serde.loads_typed(("msgpack", b"plaintext"))

    assert serde.loads_typed(("msgpack+aes", b"ciphertext")) == {"ok": True}
    assert inner.loads == [("msgpack+aes", b"ciphertext")]


def test_async_saver_factory_requires_key_before_connecting(monkeypatch):
    monkeypatch.delenv(CHECKPOINT_KEY_ENV, raising=False)

    class AsyncConnection:
        @staticmethod
        async def connect(*args, **kwargs):
            raise AssertionError("database connection must not be opened without the key")

    psycopg = ModuleType("psycopg")
    psycopg.__path__ = []
    psycopg.AsyncConnection = AsyncConnection
    rows = ModuleType("psycopg.rows")
    rows.dict_row = object()

    langgraph = ModuleType("langgraph")
    langgraph.__path__ = []
    checkpoint = ModuleType("langgraph.checkpoint")
    checkpoint.__path__ = []
    postgres = ModuleType("langgraph.checkpoint.postgres")
    postgres.__path__ = []
    aio = ModuleType("langgraph.checkpoint.postgres.aio")
    aio.AsyncPostgresSaver = object()

    monkeypatch.setitem(sys.modules, "psycopg", psycopg)
    monkeypatch.setitem(sys.modules, "psycopg.rows", rows)
    monkeypatch.setitem(sys.modules, "langgraph", langgraph)
    monkeypatch.setitem(sys.modules, "langgraph.checkpoint", checkpoint)
    monkeypatch.setitem(sys.modules, "langgraph.checkpoint.postgres", postgres)
    monkeypatch.setitem(sys.modules, "langgraph.checkpoint.postgres.aio", aio)

    with pytest.raises(CheckpointConfigurationError, match="refusing plaintext checkpoints"):
        async def run():
            async with checkpointing.async_postgres_saver("postgresql://example.invalid/db"):
                pass
        import asyncio
        asyncio.run(run())



class FakeCheckpointSerde:
    def dumps_typed(self, value):
        payload = json.dumps(value, sort_keys=True).encode("utf-8")
        return ("json+aes", base64.b64encode(payload))

    def loads_typed(self, payload):
        type_tag, encoded = payload
        assert type_tag.endswith("+aes")
        return json.loads(base64.b64decode(encoded).decode("utf-8"))


def test_checkpoint_preparation_encrypts_primitive_channels_and_pending_sends():
    serde = FakeCheckpointSerde()
    checkpoint = {
        "id": "a" * 32,
        "channel_values": {
            "goal": "SENSITIVE_GOAL_CANARY",
            "status": "running",
            "http_status": 202,
            "approved": True,
            "fallback_queue": [{"tool": "execute_payment", "arguments": {"recipient": "FALLBACK_CANARY"}}],
        },
        "pending_sends": [{"args": {"customer": "PENDING_SEND_CANARY"}}],
    }

    prepared = _prepare_checkpoint(checkpoint, serde)

    assert prepared["channel_values"]["goal"][0].startswith("__agentbridge_encrypted_primitive")
    assert prepared["channel_values"]["status"][0].startswith("__agentbridge_encrypted_primitive")
    assert prepared["channel_values"]["http_status"][0].startswith("__agentbridge_encrypted_primitive")
    assert prepared["channel_values"]["approved"][0].startswith("__agentbridge_encrypted_primitive")
    assert "SENSITIVE_GOAL_CANARY" not in json.dumps(prepared["pending_sends"])
    assert "PENDING_SEND_CANARY" not in json.dumps(prepared["pending_sends"])
    # Structured channels are non-primitive and the base saver routes them to serde blobs.
    assert prepared["channel_values"]["fallback_queue"] == checkpoint["channel_values"]["fallback_queue"]


def test_checkpoint_restore_recovers_encrypted_channels_and_pending_sends():
    serde = FakeCheckpointSerde()
    checkpoint = {
        "id": "a" * 32,
        "channel_values": {"goal": "SENSITIVE_GOAL_CANARY"},
        "pending_sends": [{"tool": "execute_payment"}],
    }
    prepared = _prepare_checkpoint(checkpoint, serde)
    saved_type = namedtuple("SavedCheckpoint", ["checkpoint", "metadata"])
    saved = saved_type(prepared, {"source": "loop", "prompt": "METADATA_CANARY"})
    restored = _restore_checkpoint(saved, serde)

    assert restored.checkpoint["channel_values"]["goal"] == "SENSITIVE_GOAL_CANARY"
    assert restored.checkpoint["pending_sends"] == [{"tool": "execute_payment"}]
    assert restored.metadata == {"source": "loop"}


def test_checkpoint_restore_rejects_legacy_inline_plaintext_channels():
    serde = FakeCheckpointSerde()
    saved_type = namedtuple("SavedCheckpoint", ["checkpoint", "metadata"])
    saved = saved_type(
        {"channel_values": {"goal": "LEGACY_PLAINTEXT_CANARY"}, "pending_sends": []},
        {"source": "input"},
    )

    with pytest.raises(ValueError, match="legacy plaintext primitive"):
        _restore_checkpoint(saved, serde)


def test_checkpoint_config_requires_opaque_run_ids():
    assert checkpoint_config("f" * 32)["configurable"]["thread_id"] == "f" * 32
    with pytest.raises(ValueError, match="opaque UUID/ULID"):
        checkpoint_config("customer-123@example.invalid")


def test_checkpoint_metadata_and_config_drop_untrusted_values():
    safe = _safe_checkpoint_metadata({
        "source": "loop",
        "step": 12,
        "parents": {"payments": "a" * 32},
        "prompt": "RAW_PROMPT_METADATA_CANARY",
        "customer_email": "user@example.invalid",
        "tags": ["RAW_TAG_CANARY"],
    })
    config = _safe_checkpoint_config({
        "configurable": {
            "thread_id": "b" * 32,
            "checkpoint_ns": "payments",
            "checkpoint_id": "c" * 32,
            "customer_email": "user@example.invalid",
        },
        "metadata": {"prompt": "RAW_CONFIG_METADATA_CANARY"},
    })

    serialized = json.dumps({"metadata": safe, "config": config})
    assert "RAW_PROMPT_METADATA_CANARY" not in serialized
    assert "RAW_TAG_CANARY" not in serialized
    assert "RAW_CONFIG_METADATA_CANARY" not in serialized
    assert "user@example.invalid" not in serialized
    assert safe == {"source": "loop", "step": 12, "parents": {"payments": "a" * 32}}
