"""Per-node execution timeout budgets."""

from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

T = TypeVar("T")


class TimeoutError(RuntimeError):
    pass


@dataclass
class TimeoutBudget:
    default_ms: int = 8000
    per_tool_ms: dict[str, int] = field(default_factory=dict)

    def ms_for(self, tool: str) -> int:
        return self.per_tool_ms.get(tool, self.default_ms)


def call_with_timeout(fn: Callable[..., T], timeout_ms: int, /, *args: Any, **kwargs: Any) -> T:
    if timeout_ms <= 0:
        return fn(*args, **kwargs)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(fn, *args, **kwargs)
        try:
            return fut.result(timeout=timeout_ms / 1000.0)
        except concurrent.futures.TimeoutError as exc:
            raise TimeoutError(f"exceeded {timeout_ms}ms") from exc
