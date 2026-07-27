"""Deterministic in-memory demo adapters.

No network, no real calendar, no real CRM. That is on purpose: the point of this
artifact is the runtime around the tools, and a demo that writes to someone's
actual calendar is a demo nobody can safely run twice.

Deterministic means: same inputs, same slots, same identifiers, every run. Tests
can assert on exact values, and a recorded demo call is reproducible. The one
input that is not a constant is ``DemoCalendar.today`` — it defaults to the real
date so the shipped adapter cannot rot into accepting yesterday, and callers that
need byte-identical output across runs inject an explicit value.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime

from voice_agent.tool_runtime import PermanentToolError

# Fixed business rules for the demo. Weekdays only, four slots a day.
OPEN_HOURS = ("09:00", "10:00", "13:00", "15:00")
_WEEKEND = (5, 6)


def _slot_id(day: date, hhmm: str) -> str:
    return f"{day.isoformat()}T{hhmm}"


def _short_hash(*parts: str) -> str:
    joined = "|".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:10]


@dataclass
class Booking:
    booking_id: str
    slot_id: str
    caller_name: str
    phone: str
    email: str | None
    reason: str | None


@dataclass
class Lead:
    lead_id: str
    caller_name: str
    phone: str
    email: str | None
    interest: str | None
    notes: str | None


@dataclass
class DemoCalendar:
    """Weekday 9/10/13/15 slots, first-come-first-served.

    ``today`` anchors the past-date rule. It defaults to the real date, resolved
    once when the instance is created, so the shipped adapter never drifts into
    accepting yesterday. Tests and reproducible demos inject an explicit value
    instead, which is what keeps them deterministic. A real adapter would take
    the tenant's timezone-aware now rather than the host's local date.
    """

    today: date = field(default_factory=date.today)
    _bookings: dict[str, Booking] = field(default_factory=dict)

    def open_slots(self, day: date) -> list[str]:
        if day < self.today:
            raise PermanentToolError("that date is in the past")
        if day.weekday() in _WEEKEND:
            return []
        return [
            _slot_id(day, hhmm) for hhmm in OPEN_HOURS if _slot_id(day, hhmm) not in self._bookings
        ]

    def slot_exists(self, slot_id: str) -> bool:
        try:
            parsed = datetime.fromisoformat(slot_id)
        except ValueError:
            return False
        day = parsed.date()
        if day < self.today or day.weekday() in _WEEKEND:
            return False
        return parsed.strftime("%H:%M") in OPEN_HOURS

    def is_booked(self, slot_id: str) -> bool:
        return slot_id in self._bookings

    @property
    def booking_count(self) -> int:
        return len(self._bookings)

    def book(
        self,
        *,
        slot_id: str,
        caller_name: str,
        phone: str,
        email: str | None,
        reason: str | None,
    ) -> Booking:
        """Reserve a slot.

        Raises ``PermanentToolError`` for both failure modes, because neither one
        gets better on a retry. The idempotency ledger stops a *duplicate* of the
        same request; this stops a *conflicting* one. Both guards are needed —
        they defend against different things.
        """
        if not self.slot_exists(slot_id):
            raise PermanentToolError("that appointment time is not one we offer")
        if slot_id in self._bookings:
            raise PermanentToolError("that time was already taken")

        booking = Booking(
            booking_id=f"bk_{_short_hash(slot_id, phone)}",
            slot_id=slot_id,
            caller_name=caller_name,
            phone=phone,
            email=email,
            reason=reason,
        )
        self._bookings[slot_id] = booking
        return booking


@dataclass
class DemoCrm:
    """Stand-in for a lead table. Append-only, deterministic identifiers."""

    leads: list[Lead] = field(default_factory=list)

    def capture(
        self,
        *,
        caller_name: str,
        phone: str,
        email: str | None,
        interest: str | None,
        notes: str | None,
    ) -> Lead:
        lead = Lead(
            lead_id=f"ld_{_short_hash(phone, caller_name)}",
            caller_name=caller_name,
            phone=phone,
            email=email,
            interest=interest,
            notes=notes,
        )
        self.leads.append(lead)
        return lead
