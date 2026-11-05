# PostgreSQL checkpoint data-protection boundary

**Implementation:** `agentbridge/core/checkpointing.py`  
**Key variable:** `AGENTBRIDGE_CHECKPOINT_ENCRYPTION_KEY_B64`  
**Status:** Draft implementation; not yet verified against a live PostgreSQL checkpoint round-trip.

## 1. Why the serializer alone is insufficient

LangGraph's PostgreSQL saver uses its serializer for non-primitive channel blobs and pending writes. Its stock write path deliberately leaves primitive channel values (strings, numbers, booleans and nulls) inline in the checkpoint JSONB document. Checkpoint metadata is also written as JSONB. A free-text goal or primitive flag can therefore bypass a serializer passed to the saver unless the persistence boundary changes the shape before the saver writes it.

This implementation subclasses the synchronous and asynchronous PostgreSQL saver at the boundary:

- Primitive channel values are wrapped in a non-primitive marker so the stock saver routes them through its serializer-backed blob table.
- Non-primitive channel values already use the serializer-backed blob path.
- Pending writes already use the serializer; the strict encrypted serializer rejects non-AES type tags.
- Non-empty pending sends are serialized and encrypted before being placed inside the checkpoint JSONB document.
- Checkpoint metadata is reduced to an explicit small contract: bounded source, bounded integer step, and parent checkpoint IDs.
- Configurable database identifiers are constrained to UUID/ULID-style opaque run and checkpoint IDs. Arbitrary caller metadata and config extensions are not copied into JSONB.
- The loader restores the encrypted values and refuses legacy inline primitive or pending-send payloads.

Implementation details must be reviewed against the installed LangGraph/checkpoint-postgres version. The upstream implementation can change; pinning and integration testing are required.

## 2. Research basis

1. **LangGraph encrypted serializer.** LangGraph provides `EncryptedSerializer.from_pycryptodome_aes` and supports injecting a serializer into checkpoint saver constructors. See the [LangGraph encrypted serializer reference](https://reference.langchain.com/python/langgraph.checkpoint/serde/encrypted/EncryptedSerializer) and the [checkpoint persistence documentation](https://docs.langchain.com/oss/python/langgraph/persistence).
2. **Serializer compatibility behavior.** The upstream encrypted serializer accepts non-encrypted type tags by falling back to its inner serializer. This implementation wraps it and rejects type tags that do not end in `+aes`, so legacy/plaintext payloads are not silently accepted. See the [upstream encrypted serializer source](https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint/langgraph/checkpoint/serde/encrypted.py).
3. **Storage path matters.** The PostgreSQL saver has separate paths for primitive values, blob values, checkpoint JSONB metadata and pending writes. The implementation adapts the write/read boundary rather than assuming every value is serialized. See the [Postgres saver implementation](https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint-postgres/langgraph/checkpoint/postgres/__init__.py) and its [shared Postgres serialization helpers](https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint-postgres/langgraph/checkpoint/postgres/base.py).
4. **Threat-model alignment.** LangGraph's threat model identifies checkpoint content, pending writes, encryption configuration and checkpoint retention as security considerations. Encryption is a control, not an assertion that every metadata field or every external persistence/logging surface is protected. See the [LangGraph threat model](https://github.com/langchain-ai/langgraph/blob/main/.github/THREAT_MODEL.md).
5. **Defence in depth.** Checkpoint encryption does not replace database TLS, least-privileged database roles, secret-manager access control, backup encryption, retention policy, or application log redaction.

## 3. Key provisioning

The runtime requires `AGENTBRIDGE_CHECKPOINT_ENCRYPTION_KEY_B64`. Its value must be Base64 encoding of exactly 32 cryptographically random bytes (256 bits). It is not a passphrase.

Generate a new key in a trusted provisioning environment:

```bash
python -c "import base64,secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())"
```

Store the generated value in a secret manager and inject it only into the checkpointing process. Do not commit it, print it in CI logs, add it to a repository variable visible to untrusted workflows, or reuse the database password.

The synchronous and asynchronous saver factories fail closed before opening a database connection when the key is missing, malformed, or the wrong length. The PostgreSQL optional dependency group includes PyCryptodome.

## 4. What remains visible in PostgreSQL

The following operational/index fields remain outside the encrypted payload by design:

- opaque thread/run ID, checkpoint ID, parent checkpoint ID and namespace;
- checkpoint timestamp and version/channel-version bookkeeping;
- channel names and the existence/count pattern of checkpoint rows;
- the small allowlisted metadata fields (`source`, bounded `step`, and parent ID mapping).

These values must not encode names, email addresses, phone numbers, customer IDs or other business identifiers. A UUID/ULID is pseudonymous and linkable, not anonymous. Database access, logs, backups and retention remain security boundaries.

The implementation does **not** establish encryption or minimization for:

- values written by code that bypasses these saver factories;
- application logs, exception reporters, external tracing vendors or arbitrary state dumps;
- callback database rows and provider payloads;
- backup exports, support bundles and manually downloaded datasets;
- deployment configuration/secret distribution or database transport unless separately configured.

## 5. Legacy compatibility and migration

This change intentionally rejects legacy plaintext primitive channel values and non-empty plaintext pending sends. Existing checkpoint rows created by the stock saver must not be read through the strict encrypted saver as though they were encrypted.

Before using this implementation against an existing checkpoint database:

1. Freeze graph writers and take an encrypted, access-controlled backup.
2. Inventory checkpoint rows, namespaces, graph versions and retention needs.
3. Build and review a one-time migration that reads legacy rows only in a controlled migration environment and re-saves them through the encrypted saver.
4. Verify that canary values are absent from plaintext JSONB, metadata, blobs and pending-write storage after migration.
5. Reconcile row counts and graph-resume behavior before enabling new writers.
6. Retain a tested rollback plan; do not fall back silently to plaintext serialization.

No migration is performed by this pull request.

## 6. Rotation, recovery and retention

- Key rotation is not implemented here. A future rotation must support decrypting old records with the old key and re-encrypting with the new key, with a controlled key-version mechanism and auditable rollout.
- Backups are only recoverable while the corresponding decryption key is retained. Key escrow/recovery needs a separately approved secret-management process.
- Encryption does not expire old checkpoints. Configure and test an explicit retention/pruning policy, deletion runbook and backup lifecycle.
- Do not delete the only key for records that must still be resumed; do not retain keys indefinitely without a documented retention basis.

## 7. Verification requirements before deployment

- Unit tests cover missing/malformed/short keys, raw 32-byte key injection, strict rejection of plaintext serializer tags, primitive wrapping/restoration, pending-send encryption, metadata filtering, opaque IDs and refusal of legacy plaintext.
- A real PostgreSQL integration test must run with the pinned dependency set and verify a full graph checkpoint write/read round-trip for goal, fallback queue, tool arguments, HITL state, callback state and pending sends.
- Query the physical tables after writing seeded canaries and prove the plaintext values do not appear in `checkpoints.checkpoint`, metadata, `checkpoint_blobs.blob` or `checkpoint_writes.blob`.
- Verify that removing/changing the key makes reads fail closed, and that database TLS, least privilege, backups and retention controls are independently configured.
- Verify both sync and async saver implementations; unit mocks alone are not enough.

**Current decision:** this is a draft storage-boundary remediation, not an end-to-end privacy certification. It has not been exercised against a real PostgreSQL service, has not migrated existing checkpoint rows, and has not provisioned a runtime key. Live money movement remains **NO-GO**.
