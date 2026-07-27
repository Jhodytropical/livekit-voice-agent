"""Structured invocation logging: one record per tool call, with sanitized args."""

import pytest
from pydantic import BaseModel

from voice_agent.tool_runtime import InvocationRecord, ToolRuntime, ToolSpec, TransientToolError
from voice_agent.tool_runtime.sanitize import sanitize_args


class _Args(BaseModel):
    caller_name: str
    phone: str
    email: str
    slot: str


async def _ok(args: _Args) -> str:
    return "bk_1"


def _capture() -> tuple[ToolRuntime, list[InvocationRecord]]:
    records: list[InvocationRecord] = []
    runtime = ToolRuntime(on_record=records.append)
    return runtime, records


@pytest.mark.asyncio
async def test_record_carries_turn_tool_key_outcome_and_latency() -> None:
    runtime, records = _capture()
    runtime.register(ToolSpec(name="book", args_model=_Args, handler=_ok, once_per_turn=True))

    outcome = await runtime.invoke(
        "book",
        turn_id="turn-42",
        raw_args={
            "caller_name": "Ada Lovelace",
            "phone": "3055550142",
            "email": "ada@example.com",
            "slot": "2026-07-28T10:00",
        },
    )

    assert len(records) == 1
    rec = records[0]
    assert rec.turn_id == "turn-42"
    assert rec.tool == "book"
    assert rec.idempotency_key == outcome.idempotency_key
    assert rec.status == "ok"
    assert rec.attempts == 1
    assert rec.latency_ms >= 0.0


@pytest.mark.asyncio
async def test_record_args_are_sanitized() -> None:
    runtime, records = _capture()
    runtime.register(ToolSpec(name="book", args_model=_Args, handler=_ok, once_per_turn=True))

    await runtime.invoke(
        "book",
        turn_id="t",
        raw_args={
            "caller_name": "Ada Lovelace",
            "phone": "3055550142",
            "email": "ada@example.com",
            "slot": "2026-07-28T10:00",
        },
    )

    logged = records[0].args
    blob = repr(logged)
    assert "ada@example.com" not in blob
    assert "3055550142" not in blob
    assert "Ada Lovelace" not in blob
    # Non-sensitive fields stay readable so the log is still useful for debugging.
    assert logged["slot"] == "2026-07-28T10:00"


@pytest.mark.asyncio
async def test_record_is_json_serializable() -> None:
    import json

    runtime, records = _capture()
    runtime.register(ToolSpec(name="book", args_model=_Args, handler=_ok, once_per_turn=True))
    await runtime.invoke(
        "book",
        turn_id="t",
        raw_args={
            "caller_name": "A",
            "phone": "+15550001111",
            "email": "a@b.com",
            "slot": "s",
        },
    )

    json.dumps(records[0].as_dict())  # must not raise


@pytest.mark.asyncio
async def test_failed_and_replayed_invocations_are_recorded() -> None:
    runtime, records = _capture()

    calls = 0

    async def flaky(args: _Args) -> str:
        nonlocal calls
        calls += 1
        raise TransientToolError("down")

    runtime.register(ToolSpec(name="book", args_model=_Args, handler=flaky, once_per_turn=True))
    payload = {"caller_name": "A", "phone": "+15550001111", "email": "a@b.com", "slot": "s"}
    await runtime.invoke("book", turn_id="t", raw_args=payload)

    assert records[-1].status == "failed"
    assert records[-1].attempts >= 1


def test_sanitizer_masks_known_sensitive_keys() -> None:
    out = sanitize_args(
        {
            "email": "jean.hyacinthe@example.com",
            "phone": "3055550142",
            "caller_name": "Ada Lovelace",
            "notes": "call back about the invoice",
            "slot": "2026-07-28T10:00",
        }
    )
    assert out["email"].endswith("[redacted]") or "example.com" not in out["email"]
    assert out["phone"].endswith("4594")
    assert out["phone"].count("*") > 0
    assert out["caller_name"] != "Ada Lovelace"
    assert out["slot"] == "2026-07-28T10:00"


def test_sanitizer_truncates_long_values() -> None:
    out = sanitize_args({"slot": "x" * 5000})
    assert len(out["slot"]) < 300
