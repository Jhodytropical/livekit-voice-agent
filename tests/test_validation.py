"""Validation: LLM-supplied tool arguments are untrusted input and are rejected
at the runtime boundary before any handler runs."""

import pytest
from pydantic import BaseModel, Field

from voice_agent.tool_runtime import MAX_RAW_ARGS_BYTES, ToolRuntime, ToolSpec


class _Args(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(min_length=1, max_length=40)
    party_size: int = Field(ge=1, le=8)


async def _never_called(args: _Args) -> str:  # pragma: no cover - must not run
    raise AssertionError("handler must not run when validation fails")


def _runtime() -> ToolRuntime:
    runtime = ToolRuntime()
    runtime.register(
        ToolSpec(name="book", args_model=_Args, handler=_never_called, once_per_turn=True)
    )
    return runtime


@pytest.mark.asyncio
async def test_missing_required_field_is_rejected_without_running_handler() -> None:
    outcome = await _runtime().invoke("book", turn_id="t", raw_args={"party_size": 2})

    assert outcome.status == "invalid"
    assert outcome.result is None
    assert outcome.attempts == 0
    assert "name" in (outcome.error or "")


@pytest.mark.asyncio
async def test_out_of_range_value_is_rejected() -> None:
    outcome = await _runtime().invoke(
        "book", turn_id="t", raw_args={"name": "Jean", "party_size": 99}
    )
    assert outcome.status == "invalid"


@pytest.mark.asyncio
async def test_unknown_field_is_rejected() -> None:
    """An LLM inventing an extra argument is a prompt-injection smell, not a typo."""
    outcome = await _runtime().invoke(
        "book",
        turn_id="t",
        raw_args={"name": "Jean", "party_size": 2, "admin_override": True},
    )
    assert outcome.status == "invalid"


@pytest.mark.asyncio
async def test_oversized_payload_is_rejected_before_parsing() -> None:
    outcome = await _runtime().invoke(
        "book",
        turn_id="t",
        raw_args={"name": "x" * (MAX_RAW_ARGS_BYTES + 100), "party_size": 2},
    )
    assert outcome.status == "invalid"
    assert "too large" in (outcome.error or "").lower()


@pytest.mark.asyncio
async def test_unregistered_tool_is_rejected() -> None:
    with pytest.raises(KeyError):
        await _runtime().invoke("wire_money", turn_id="t", raw_args={})


@pytest.mark.asyncio
async def test_validation_error_message_does_not_echo_raw_values() -> None:
    """Error strings go back to the LLM and into logs — they must not carry the
    caller's raw payload."""
    outcome = await _runtime().invoke(
        "book", turn_id="t", raw_args={"name": "", "party_size": 2, "secret": "sk-live-123456"}
    )
    assert outcome.status == "invalid"
    assert "sk-live-123456" not in (outcome.error or "")
