"""Idempotency ledger.

The interface deliberately exposes a single method, ``run_once``. A two-call
"reserve then record" API is easy to get wrong — a caller who forgets the second
call silently loses the guarantee, and that failure only shows up as a
double-booked calendar in production. Here the ledger owns the execution, so
there is nothing to forget.

The in-memory implementation is the demo-grade one. In production, swap in a
Redis or Postgres implementation of the same Protocol; the runtime does not
change. See README, "Swapping the ledger".
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar, runtime_checkable

T = TypeVar("T")

# Idempotency keys are opaque and fixed-width so they are safe to log and to use
# as a database primary key.
_KEY_PREFIX = "tk_"
_KEY_HEX_LEN = 32


def derive_key(*, tool: str, turn_id: str, args: Mapping[str, Any]) -> str:
    """Derive a deterministic idempotency key for one tool call in one call-turn.

    The key covers the tool name, the turn, and the *validated* arguments. Two
    identical requests inside one turn collide (and therefore execute once);
    a genuinely different request in the same turn does not.

    Callers never construct keys by hand — ``ToolRuntime`` derives them — so a
    caller cannot accidentally reuse a key across turns or omit an argument.
    """
    canonical = json.dumps(
        {"tool": tool, "turn_id": turn_id, "args": args},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:_KEY_HEX_LEN]
    return f"{_KEY_PREFIX}{digest}"


@runtime_checkable
class IdempotencyLedger(Protocol):
    """Guarantees at-most-once execution per key."""

    async def run_once(self, key: str, factory: Callable[[], Awaitable[T]]) -> tuple[T, bool]:
        """Run ``factory`` at most once for ``key``.

        Returns ``(result, executed)``. ``executed`` is ``False`` when the result
        came from the ledger instead of a fresh execution.

        Concurrent calls with the same key must not both execute: later callers
        wait for the first and receive its result.

        A ``factory`` that raises must *not* be cached. A transient outage should
        not permanently poison the key — the exception propagates and the next
        call is free to try again.
        """
        ...


@dataclass
class _Entry:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    has_result: bool = False
    result: Any = None
    created_at: float = field(default_factory=time.time)


class InMemoryIdempotencyLedger:
    """Process-local ledger. Correct within one agent process, which is the
    boundary a single voice session lives in.

    Not durable and not shared across workers — see README for the production
    swap. ``max_entries`` bounds memory so a long call (or a hostile caller
    generating unique args) cannot grow this without limit.
    """

    def __init__(self, *, max_entries: int = 1024) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._max_entries = max_entries
        self._entries: dict[str, _Entry] = {}
        self._guard = asyncio.Lock()

    async def run_once(self, key: str, factory: Callable[[], Awaitable[T]]) -> tuple[T, bool]:
        async with self._guard:
            entry = self._entries.get(key)
            if entry is None:
                self._evict_if_needed()
                entry = _Entry()
                self._entries[key] = entry

        async with entry.lock:
            if entry.has_result:
                return entry.result, False
            result = await factory()  # may raise — deliberately not cached
            entry.has_result = True
            entry.result = result
            return result, True

    def _evict_if_needed(self) -> None:
        while len(self._entries) >= self._max_entries:
            oldest = min(self._entries, key=lambda k: self._entries[k].created_at)
            del self._entries[oldest]

    def __len__(self) -> int:
        return len(self._entries)
