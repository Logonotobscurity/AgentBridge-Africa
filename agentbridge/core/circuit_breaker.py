"""Circuit breaker wrapping external provider calls.

Prevents retry storms during M-Pesa / Paystack / bank outages.
States: closed → open → half_open → closed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, TypeVar

T = TypeVar("T")


class CircuitOpenError(RuntimeError):
    pass


@dataclass
class CircuitBreaker:
    name: str
    failure_threshold: int = 3
    recovery_timeout_s: float = 30.0
    half_open_max_calls: int = 1
    failure_count: int = 0
    state: str = "closed"
    opened_at: float = 0.0
    half_open_calls: int = 0
    last_error: str | None = None

    def call(self, fn: Callable[[], T]) -> T:
        self._maybe_half_open()
        if self.state == "open":
            raise CircuitOpenError(f"circuit_open:{self.name}")
        if self.state == "half_open" and self.half_open_calls >= self.half_open_max_calls:
            raise CircuitOpenError(f"circuit_open:{self.name}")
        try:
            if self.state == "half_open":
                self.half_open_calls += 1
            result = fn()
            self._on_success()
            return result
        except CircuitOpenError:
            raise
        except Exception as exc:
            self._on_failure(str(exc))
            raise

    def _maybe_half_open(self) -> None:
        if self.state == "open" and (time.monotonic() - self.opened_at) >= self.recovery_timeout_s:
            self.state = "half_open"
            self.half_open_calls = 0

    def _on_success(self) -> None:
        self.failure_count = 0
        self.state = "closed"
        self.half_open_calls = 0
        self.last_error = None

    def _on_failure(self, error: str) -> None:
        self.failure_count += 1
        self.last_error = error
        if self.state == "half_open" or self.failure_count >= self.failure_threshold:
            self.state = "open"
            self.opened_at = time.monotonic()
