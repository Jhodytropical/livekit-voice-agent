"""Retry: bounded attempts with exponential backoff, and only for transient faults."""

import pytest
from pydantic import BaseModel

from voice_agent.tool_runtime import (
    PermanentToolError,
    RetryPolicy,
    ToolRuntime,
    ToolSpec,
    TransientToolError,
)


class _Args(BaseModel):
    slot: str


def _runtime(policy: RetryPolicy, sleeps: list[float]) -> ToolRuntime:
    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    return ToolRuntime(retry=policy, sleep=fake_sleep)


@pytest.mark.asyncio
async def test_transient_failure_is_retried_then_succeeds() -> None:
    sleeps: list[float] = []
    attempts = 0

    async def handler(args: _Args) -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise TransientToolError("calendar timed out")
        return "booked"

    runtime = _runtime(RetryPolicy(max_attempts=3, base_delay=0.2, max_delay=2.0), sleeps)
    runtime.register(ToolSpec(name="book", args_model=_Args, handler=handler))

    outcome = await runtime.invoke("book", turn_id="t", raw_args={"slot": "s"})

    assert outcome.status == "ok"
    assert outcome.result == "booked"
    assert outcome.attempts == 3
    assert sleeps == [0.2, 0.4], "expected exponential backoff between attempts"


@pytest.mark.asyncio
async def test_retries_are_bounded_by_max_attempts() -> None:
    sleeps: list[float] = []
    attempts = 0

    async def handler(args: _Args) -> str:
        nonlocal attempts
        attempts += 1
        raise TransientToolError("still down")

    runtime = _runtime(RetryPolicy(max_attempts=3, base_delay=0.1, max_delay=2.0), sleeps)
    runtime.register(ToolSpec(name="book", args_model=_Args, handler=handler))

    outcome = await runtime.invoke("book", turn_id="t", raw_args={"slot": "s"})

    assert outcome.status == "failed"
    assert attempts == 3, "must not retry forever"
    assert len(sleeps) == 2


@pytest.mark.asyncio
async def test_backoff_is_capped_at_max_delay() -> None:
    sleeps: list[float] = []

    async def handler(args: _Args) -> str:
        raise TransientToolError("down")

    runtime = _runtime(RetryPolicy(max_attempts=5, base_delay=1.0, max_delay=2.0), sleeps)
    runtime.register(ToolSpec(name="book", args_model=_Args, handler=handler))

    await runtime.invoke("book", turn_id="t", raw_args={"slot": "s"})

    assert sleeps == [1.0, 2.0, 2.0, 2.0]


@pytest.mark.asyncio
async def test_permanent_failure_is_not_retried() -> None:
    sleeps: list[float] = []
    attempts = 0

    async def handler(args: _Args) -> str:
        nonlocal attempts
        attempts += 1
        raise PermanentToolError("that slot no longer exists")

    runtime = _runtime(RetryPolicy(max_attempts=3, base_delay=0.1), sleeps)
    runtime.register(ToolSpec(name="book", args_model=_Args, handler=handler))

    outcome = await runtime.invoke("book", turn_id="t", raw_args={"slot": "s"})

    assert outcome.status == "failed"
    assert attempts == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_unexpected_exception_is_not_retried_and_does_not_leak_details() -> None:
    attempts = 0

    async def handler(args: _Args) -> str:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("psycopg2: password authentication failed for user 'crm'")

    runtime = ToolRuntime(retry=RetryPolicy(max_attempts=3))
    runtime.register(ToolSpec(name="book", args_model=_Args, handler=handler))

    outcome = await runtime.invoke("book", turn_id="t", raw_args={"slot": "s"})

    assert outcome.status == "failed"
    assert attempts == 1
    assert "password" not in (outcome.error or "")


@pytest.mark.asyncio
async def test_handler_timeout_is_treated_as_transient() -> None:
    import asyncio

    attempts = 0

    async def handler(args: _Args) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            await asyncio.sleep(10)
        return "booked"

    sleeps: list[float] = []
    runtime = _runtime(RetryPolicy(max_attempts=2, base_delay=0.01, timeout=0.05), sleeps)
    runtime.register(ToolSpec(name="book", args_model=_Args, handler=handler))

    outcome = await runtime.invoke("book", turn_id="t", raw_args={"slot": "s"})

    assert outcome.status == "ok"
    assert attempts == 2


def test_retry_policy_rejects_unbounded_configuration() -> None:
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=0)
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=100)
