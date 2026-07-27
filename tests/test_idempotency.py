"""Idempotency: the same call-turn key must execute the handler exactly once."""

import asyncio

import pytest
from pydantic import BaseModel

from voice_agent.tool_runtime import InMemoryIdempotencyLedger, ToolRuntime, ToolSpec


class _Args(BaseModel):
    slot: str


@pytest.mark.asyncio
async def test_duplicate_key_executes_handler_once_and_replays_result() -> None:
    calls: list[str] = []

    async def handler(args: _Args) -> dict[str, str]:
        calls.append(args.slot)
        return {"booking_id": f"bk_{len(calls)}"}

    runtime = ToolRuntime()
    runtime.register(ToolSpec(name="book", args_model=_Args, handler=handler, once_per_turn=True))

    first = await runtime.invoke("book", turn_id="turn-1", raw_args={"slot": "2026-07-28T10:00"})
    second = await runtime.invoke("book", turn_id="turn-1", raw_args={"slot": "2026-07-28T10:00"})

    assert calls == ["2026-07-28T10:00"], "handler ran more than once for the same key"
    assert first.status == "ok"
    assert second.status == "replayed"
    assert first.idempotency_key == second.idempotency_key
    assert second.result == first.result


@pytest.mark.asyncio
async def test_concurrent_duplicate_calls_execute_once() -> None:
    """A double-fire inside one turn is the real-world failure mode: two overlapping
    tool calls, not two sequential ones."""
    started = 0

    async def handler(args: _Args) -> dict[str, str]:
        nonlocal started
        started += 1
        await asyncio.sleep(0.05)
        return {"booking_id": "bk_1"}

    runtime = ToolRuntime()
    runtime.register(ToolSpec(name="book", args_model=_Args, handler=handler, once_per_turn=True))

    outcomes = await asyncio.gather(
        runtime.invoke("book", turn_id="t", raw_args={"slot": "s1"}),
        runtime.invoke("book", turn_id="t", raw_args={"slot": "s1"}),
        runtime.invoke("book", turn_id="t", raw_args={"slot": "s1"}),
    )

    assert started == 1
    assert sorted(o.status for o in outcomes) == ["ok", "replayed", "replayed"]


@pytest.mark.asyncio
async def test_different_turns_are_separate_executions() -> None:
    calls = 0

    async def handler(args: _Args) -> str:
        nonlocal calls
        calls += 1
        return "done"

    runtime = ToolRuntime()
    runtime.register(ToolSpec(name="book", args_model=_Args, handler=handler, once_per_turn=True))

    await runtime.invoke("book", turn_id="turn-1", raw_args={"slot": "s"})
    await runtime.invoke("book", turn_id="turn-2", raw_args={"slot": "s"})

    assert calls == 2


@pytest.mark.asyncio
async def test_read_only_tool_is_not_ledger_guarded() -> None:
    calls = 0

    async def handler(args: _Args) -> str:
        nonlocal calls
        calls += 1
        return "fresh"

    runtime = ToolRuntime()
    runtime.register(ToolSpec(name="check", args_model=_Args, handler=handler, once_per_turn=False))

    await runtime.invoke("check", turn_id="t", raw_args={"slot": "s"})
    await runtime.invoke("check", turn_id="t", raw_args={"slot": "s"})

    assert calls == 2, "read-only tools should re-run; only writes are ledger-guarded"


@pytest.mark.asyncio
async def test_failed_execution_does_not_poison_the_key() -> None:
    """A failure must leave the key reusable — otherwise one transient outage
    permanently blocks the caller from ever booking that slot in that turn."""
    attempts = 0

    async def handler(args: _Args) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("downstream exploded")
        return "ok"

    runtime = ToolRuntime()
    runtime.register(ToolSpec(name="book", args_model=_Args, handler=handler, once_per_turn=True))

    first = await runtime.invoke("book", turn_id="t", raw_args={"slot": "s"})
    assert first.status == "failed"

    second = await runtime.invoke("book", turn_id="t", raw_args={"slot": "s"})
    assert second.status == "ok"


@pytest.mark.asyncio
async def test_ledger_run_once_returns_executed_flag() -> None:
    ledger = InMemoryIdempotencyLedger()
    calls = 0

    async def factory() -> int:
        nonlocal calls
        calls += 1
        return 42

    value, executed = await ledger.run_once("k", factory)
    assert (value, executed) == (42, True)

    value, executed = await ledger.run_once("k", factory)
    assert (value, executed) == (42, False)
    assert calls == 1
