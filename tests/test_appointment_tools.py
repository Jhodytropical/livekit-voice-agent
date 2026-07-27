"""The three niche tools, exercised through the runtime exactly as the agent calls them."""

from datetime import date, timedelta

import pytest

from voice_agent.appointments import (
    DemoCalendar,
    DemoCrm,
    build_appointment_runtime,
)
from voice_agent.tool_runtime import InvocationRecord, PermanentToolError

BOOK_ARGS = {
    "slot_id": "2026-07-28T10:00",
    "caller_name": "Ada Lovelace",
    "phone": "3055550142",
    "email": "ada@example.com",
    "reason": "roof inspection quote",
}


def _runtime() -> tuple[object, DemoCalendar, DemoCrm, list[InvocationRecord]]:
    calendar = DemoCalendar(today=date(2026, 7, 27))
    crm = DemoCrm()
    records: list[InvocationRecord] = []
    runtime = build_appointment_runtime(calendar=calendar, crm=crm, on_record=records.append)
    return runtime, calendar, crm, records


@pytest.mark.asyncio
async def test_check_availability_returns_deterministic_slots() -> None:
    runtime, _, _, _ = _runtime()

    first = await runtime.invoke(
        "check_availability", turn_id="t1", raw_args={"date": "2026-07-28"}
    )
    second = await runtime.invoke(
        "check_availability", turn_id="t2", raw_args={"date": "2026-07-28"}
    )

    assert first.ok
    assert first.result["slots"], "expected at least one slot on a weekday"
    assert first.result == second.result, "demo adapter must be deterministic"


@pytest.mark.asyncio
async def test_check_availability_rejects_a_date_in_the_past() -> None:
    runtime, _, _, _ = _runtime()
    outcome = await runtime.invoke(
        "check_availability", turn_id="t", raw_args={"date": "2020-01-01"}
    )
    assert outcome.status == "failed"


def test_default_calendar_tracks_the_real_date() -> None:
    """The shipped adapter must not keep accepting yesterday after the demo date passes."""
    calendar = DemoCalendar()

    with pytest.raises(PermanentToolError, match="past"):
        calendar.open_slots(date.today() - timedelta(days=1))


@pytest.mark.asyncio
async def test_check_availability_returns_no_slots_on_a_weekend() -> None:
    runtime, _, _, _ = _runtime()
    # 2026-08-01 is a Saturday.
    outcome = await runtime.invoke(
        "check_availability", turn_id="t", raw_args={"date": "2026-08-01"}
    )
    assert outcome.ok
    assert outcome.result["slots"] == []


@pytest.mark.asyncio
async def test_book_appointment_reserves_the_slot() -> None:
    runtime, calendar, _, _ = _runtime()

    outcome = await runtime.invoke("book_appointment", turn_id="t1", raw_args=BOOK_ARGS)

    assert outcome.status == "ok"
    assert outcome.result["slot_id"] == BOOK_ARGS["slot_id"]
    assert outcome.result["booking_id"].startswith("bk_")
    assert calendar.is_booked(BOOK_ARGS["slot_id"])


@pytest.mark.asyncio
async def test_repeated_booking_in_one_turn_does_not_double_book() -> None:
    """The idempotency guard: a stuttering LLM emitting the tool call twice in one
    turn must produce one booking, and the caller must hear the same confirmation."""
    runtime, calendar, _, _ = _runtime()

    first = await runtime.invoke("book_appointment", turn_id="turn-1", raw_args=BOOK_ARGS)
    second = await runtime.invoke("book_appointment", turn_id="turn-1", raw_args=BOOK_ARGS)

    assert first.status == "ok"
    assert second.status == "replayed"
    assert first.result["booking_id"] == second.result["booking_id"]
    assert calendar.booking_count == 1


@pytest.mark.asyncio
async def test_booking_the_same_slot_in_a_later_turn_is_rejected_by_the_calendar() -> None:
    """The business guard, distinct from idempotency: a *different* caller in a
    *different* turn must be told the slot is gone, not silently double-booked."""
    runtime, calendar, _, _ = _runtime()

    await runtime.invoke("book_appointment", turn_id="turn-1", raw_args=BOOK_ARGS)
    second = await runtime.invoke(
        "book_appointment",
        turn_id="turn-2",
        raw_args={**BOOK_ARGS, "caller_name": "Someone Else", "phone": "+13055550123"},
    )

    assert second.status == "failed"
    assert "already" in (second.error or "").lower()
    assert calendar.booking_count == 1


@pytest.mark.asyncio
async def test_booking_an_unknown_slot_is_rejected_without_retrying() -> None:
    runtime, _, _, _ = _runtime()
    outcome = await runtime.invoke(
        "book_appointment", turn_id="t", raw_args={**BOOK_ARGS, "slot_id": "2026-07-28T03:00"}
    )
    assert outcome.status == "failed"
    assert outcome.attempts == 1, "a nonexistent slot is permanent, not transient"


@pytest.mark.asyncio
async def test_booking_rejects_a_malformed_phone_number() -> None:
    runtime, _, _, _ = _runtime()
    outcome = await runtime.invoke(
        "book_appointment", turn_id="t", raw_args={**BOOK_ARGS, "phone": "call me maybe"}
    )
    assert outcome.status == "invalid"


@pytest.mark.asyncio
async def test_capture_lead_stores_once_per_turn() -> None:
    runtime, _, crm, _ = _runtime()
    args = {
        "caller_name": "Ada Lovelace",
        "phone": "3055550142",
        "interest": "commercial roof replacement",
    }

    first = await runtime.invoke("capture_lead", turn_id="t1", raw_args=args)
    second = await runtime.invoke("capture_lead", turn_id="t1", raw_args=args)

    assert first.status == "ok"
    assert second.status == "replayed"
    assert len(crm.leads) == 1
    assert first.result["lead_id"] == second.result["lead_id"]


@pytest.mark.asyncio
async def test_every_invocation_emits_one_sanitized_record() -> None:
    runtime, _, _, records = _runtime()

    await runtime.invoke("check_availability", turn_id="t", raw_args={"date": "2026-07-28"})
    await runtime.invoke("book_appointment", turn_id="t", raw_args=BOOK_ARGS)

    assert [r.tool for r in records] == ["check_availability", "book_appointment"]
    blob = repr([r.args for r in records])
    assert "3055550142" not in blob
    assert "ada@example.com" not in blob
    assert all(r.idempotency_key.startswith("tk_") for r in records)


def test_runtime_registers_exactly_the_three_niche_tools() -> None:
    runtime = build_appointment_runtime(calendar=DemoCalendar(), crm=DemoCrm())
    assert runtime.tool_names == ("book_appointment", "capture_lead", "check_availability")
