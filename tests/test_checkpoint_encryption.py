import base64
import sys
from types import ModuleType

import pytest

from agentbridge.core import checkpointing
from agentbridge.core.checkpointing import (
    _StrictEncryptedCheckpointSerializer,
    CHECKPOINT_KEY_ENV,
    CheckpointConfigurationError,
    _checkpoint_serde,
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
