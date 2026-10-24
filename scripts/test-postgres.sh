#!/usr/bin/env bash
set -euo pipefail

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required for the PostgreSQL integration harness" >&2
  exit 2
fi

POSTGRES_IMAGE="${POSTGRES_IMAGE:-postgres:16-alpine}"
POSTGRES_PORT="${POSTGRES_PORT:-55432}"
CONTAINER_NAME="agentbridge-postgres-test-$$"

cleanup() {
  docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

docker run --rm -d \
  --name "${CONTAINER_NAME}" \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=agentbridge_test \
  -p "127.0.0.1:${POSTGRES_PORT}:5432" \
  "${POSTGRES_IMAGE}" >/dev/null

for _ in $(seq 1 30); do
  if docker exec "${CONTAINER_NAME}" pg_isready -U postgres -d agentbridge_test >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

docker exec "${CONTAINER_NAME}" pg_isready -U postgres -d agentbridge_test >/dev/null

export AGENTBRIDGE_TEST_POSTGRES_DSN="postgresql://postgres:postgres@localhost:${POSTGRES_PORT}/agentbridge_test"
PYTHON_BIN="${PYTHON_BIN:-python}"
"${PYTHON_BIN}" -m pytest -q tests/integration/test_payment_concurrency.py
