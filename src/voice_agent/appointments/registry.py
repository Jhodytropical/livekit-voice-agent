"""Wires the three niche tools into a ToolRuntime.

Which tools are ledger-guarded is the whole design decision here:

* ``check_availability`` — read-only. ``once_per_turn=False`` so a caller who
  asks twice gets current data rather than a stale replay.
* ``book_appointment`` — writes. ``once_per_turn=True``.
* ``capture_lead`` — writes. ``once_per_turn=True``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from voice_agent.tool_runtime import (
    InvocationRecord,
    RetryPolicy,
    ToolRuntime,
    ToolSpec,
)

from .adapters import DemoCalendar, DemoCrm
from .schemas import BookAppointmentArgs, CaptureLeadArgs, CheckAvailabilityArgs


def build_appointment_runtime(
    *,
    calendar: DemoCalendar,
    crm: DemoCrm,
    retry: RetryPolicy | None = None,
    on_record: Callable[[InvocationRecord], None] | None = None,
) -> ToolRuntime:
    async def check_availability(args: CheckAvailabilityArgs) -> dict[str, Any]:
        slots = calendar.open_slots(args.date)
        return {"date": args.date.isoformat(), "slots": slots}

    async def book_appointment(args: BookAppointmentArgs) -> dict[str, Any]:
        booking = calendar.book(
            slot_id=args.slot_id,
            caller_name=args.caller_name,
            phone=args.phone,
            email=args.email,
            reason=args.reason,
        )
        return {"booking_id": booking.booking_id, "slot_id": booking.slot_id}

    async def capture_lead(args: CaptureLeadArgs) -> dict[str, Any]:
        lead = crm.capture(
            caller_name=args.caller_name,
            phone=args.phone,
            email=args.email,
            interest=args.interest,
            notes=args.notes,
        )
        return {"lead_id": lead.lead_id}

    kwargs: dict[str, Any] = {"retry": retry or RetryPolicy()}
    if on_record is not None:
        kwargs["on_record"] = on_record
    runtime = ToolRuntime(**kwargs)

    runtime.register(
        ToolSpec(
            name="check_availability",
            args_model=CheckAvailabilityArgs,
            handler=check_availability,
            once_per_turn=False,
        )
    )
    runtime.register(
        ToolSpec(
            name="book_appointment",
            args_model=BookAppointmentArgs,
            handler=book_appointment,
            once_per_turn=True,
        )
    )
    runtime.register(
        ToolSpec(
            name="capture_lead",
            args_model=CaptureLeadArgs,
            handler=capture_lead,
            once_per_turn=True,
        )
    )
    return runtime
