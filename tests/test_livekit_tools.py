"""The LiveKit function-tool wrappers.

These are adapter tests: they check that a LiveKit tool call reaches the runtime
with the right turn id, that outcomes map onto return values and ToolError, and
that mutating tools disable interruptions. The runtime's own guarantees are
covered in the other test modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from livekit.agents.llm import ToolError

from voice_agent.appointment_agent import AppointmentAgent, build_agent
from voice_agent.appointments import DemoCalendar, DemoCrm, build_appointment_runtime
from voice_agent.config import Settings


@dataclass
class _FakeSpeechHandle:
    id: str = "speech-1"


@dataclass
class _FakeRunContext:
    """Stands in for RunContext. Only the two members the wrappers touch."""

    speech_handle: _FakeSpeechHandle = field(default_factory=_FakeSpeechHandle)
    function_call: object | None = None
    interruptions_disallowed: int = 0

    def disallow_interruptions(self) -> None:
        self.interruptions_disallowed += 1


BOOK_ARGS = {
    "slot_id": "2026-07-28T10:00",
    "caller_name": "Ada Lovelace",
    "phone": "3055550142",
    "email": "ada@example.com",
    "reason": "roof inspection",
}


#: book_appointment refuses to write unless the agent's own recent speech contains a
#: matching read-back (see readback.py, run 5 defect 6). These tests are about turn and
#: idempotency behaviour, so they stand in a correct read-back and let that check pass.
READBACK = "To confirm, three oh five, five five five, oh one four two — is that right?"


def _agent(*, readback: str | None = READBACK) -> tuple[AppointmentAgent, DemoCalendar, DemoCrm]:
    calendar, crm = DemoCalendar(), DemoCrm()
    runtime = build_appointment_runtime(calendar=calendar, crm=crm)
    agent = AppointmentAgent(runtime=runtime)
    agent._recent_assistant_messages = lambda: [readback] if readback else []  # type: ignore[method-assign]
    return agent, calendar, crm


@pytest.mark.asyncio
async def test_a_booking_without_a_readback_is_refused() -> None:
    """The structural half of defect 6: no read-back, no write."""
    agent, calendar, _ = _agent(readback=None)

    with pytest.raises(ToolError, match="read the phone number back"):
        await agent.book_appointment(_FakeRunContext(), **BOOK_ARGS)

    assert calendar.booking_count == 0


@pytest.mark.asyncio
async def test_a_booking_that_contradicts_the_readback_is_refused() -> None:
    """Ten digits read back, eleven written — run 5, session 1, verbatim."""
    agent, calendar, _ = _agent(readback="To confirm: 5 5 5 5 5 5 5 5 5 5, correct?")

    with pytest.raises(ToolError, match="does not match"):
        await agent.book_appointment(_FakeRunContext(), **{**BOOK_ARGS, "phone": "55555555555"})

    assert calendar.booking_count == 0


def test_agent_exposes_exactly_the_three_niche_tools() -> None:
    agent = build_agent(Settings())
    assert sorted(tool.id for tool in agent.tools) == [
        "book_appointment",
        "capture_lead",
        "check_availability",
    ]


@pytest.mark.asyncio
async def test_check_availability_returns_slots_to_the_model() -> None:
    agent, _, _ = _agent()
    result = await agent.check_availability(_FakeRunContext(), date="2026-07-28")

    assert result["slots"]
    assert result["already_recorded"] is False


@pytest.mark.asyncio
async def test_book_appointment_disallows_interruptions_before_writing() -> None:
    agent, calendar, _ = _agent()
    ctx = _FakeRunContext()

    await agent.book_appointment(ctx, **BOOK_ARGS)

    assert ctx.interruptions_disallowed == 1
    assert calendar.booking_count == 1


@pytest.mark.asyncio
async def test_repeated_tool_call_in_one_speech_turn_books_once() -> None:
    """Same speech handle means same turn, so a model that emits the call twice
    while producing one reply still writes one booking."""
    agent, calendar, _ = _agent()
    ctx = _FakeRunContext(speech_handle=_FakeSpeechHandle("speech-7"))

    first = await agent.book_appointment(ctx, **BOOK_ARGS)
    second = await agent.book_appointment(ctx, **BOOK_ARGS)

    assert calendar.booking_count == 1
    assert first["booking_id"] == second["booking_id"]
    assert first["already_recorded"] is False
    assert second["already_recorded"] is True


@pytest.mark.asyncio
async def test_a_later_turn_booking_the_same_slot_raises_tool_error() -> None:
    agent, calendar, _ = _agent()

    await agent.book_appointment(_FakeRunContext(_FakeSpeechHandle("speech-1")), **BOOK_ARGS)

    with pytest.raises(ToolError):
        await agent.book_appointment(
            _FakeRunContext(_FakeSpeechHandle("speech-2")),
            **{**BOOK_ARGS, "caller_name": "Other Caller", "phone": "+13055550123"},
        )
    assert calendar.booking_count == 1


@pytest.mark.asyncio
async def test_invalid_arguments_raise_tool_error_the_model_can_recover_from() -> None:
    agent, calendar, _ = _agent()

    with pytest.raises(ToolError) as excinfo:
        await agent.book_appointment(
            _FakeRunContext(), **{**BOOK_ARGS, "phone": "whatever they said"}
        )

    assert "phone" in str(excinfo.value)
    assert calendar.booking_count == 0


@pytest.mark.asyncio
async def test_tool_error_does_not_leak_the_callers_data() -> None:
    agent, _, _ = _agent()

    with pytest.raises(ToolError) as excinfo:
        await agent.book_appointment(
            _FakeRunContext(), **{**BOOK_ARGS, "email": "not-an-email", "phone": "3055550142"}
        )

    message = str(excinfo.value)
    assert "not-an-email" not in message
    assert "3055550142" not in message


@pytest.mark.asyncio
async def test_capture_lead_records_a_lead_and_disallows_interruptions() -> None:
    agent, _, crm = _agent()
    ctx = _FakeRunContext()

    result = await agent.capture_lead(
        ctx, caller_name="Jean", phone="3055550142", interest="roof replacement"
    )

    assert result["lead_id"].startswith("ld_")
    assert len(crm.leads) == 1
    assert ctx.interruptions_disallowed == 1


@pytest.mark.asyncio
async def test_turn_id_falls_back_to_the_function_call_id() -> None:
    """A RunContext without a speech handle must still produce a usable key
    rather than silently sharing one across every call."""

    @dataclass
    class _Call:
        call_id: str

    agent, calendar, _ = _agent()
    ctx = _FakeRunContext(speech_handle=None, function_call=_Call("call-abc"))  # type: ignore[arg-type]

    await agent.book_appointment(ctx, **BOOK_ARGS)
    assert calendar.booking_count == 1


# --- date anchoring -------------------------------------------------------
#
# Regression for the first live call (2026-07-27): with no date in the prompt the
# model called check_availability with 2024-06-03 for "today" and 2024-06-05 for
# "tomorrow". The calendar guard caught both, so nothing was hallucinated — but the
# agent could not book anything, because every relative date a caller uses was
# unresolvable. These tests fail if the anchor is ever dropped or drifts from the
# calendar's own notion of today.


def test_instructions_state_todays_date() -> None:
    from datetime import date

    from voice_agent.appointment_agent import build_instructions

    text = build_instructions(date(2026, 7, 27))
    assert "2026-07-27" in text
    assert "Monday" in text
    assert "2026-07-28" in text  # tomorrow, resolved for the model
    assert "YYYY-MM-DD" in text


def test_instructions_tomorrow_crosses_month_and_year_boundaries() -> None:
    from datetime import date

    from voice_agent.appointment_agent import build_instructions

    assert "2027-01-01" in build_instructions(date(2026, 12, 31))


def test_agent_prompt_and_calendar_share_one_today() -> None:
    """The prompt's date and the past-date guard must come from the same value.

    If these ever diverge the agent offers dates its own calendar then rejects,
    which is worse than having no anchor at all — it fails mid-call, not up front.
    """
    agent = build_agent(Settings())
    assert agent.today.isoformat() in agent.instructions


def test_agent_defaults_to_the_real_date() -> None:
    from datetime import date

    runtime = build_appointment_runtime(calendar=DemoCalendar(), crm=DemoCrm())
    assert AppointmentAgent(runtime=runtime).today == date.today()
