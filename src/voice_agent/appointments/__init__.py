"""Inbound appointment-booking niche: schemas, demo adapters, tool wiring."""

from .adapters import OPEN_HOURS, Booking, DemoCalendar, DemoCrm, Lead
from .registry import build_appointment_runtime
from .schemas import BookAppointmentArgs, CaptureLeadArgs, CheckAvailabilityArgs

__all__ = [
    "OPEN_HOURS",
    "BookAppointmentArgs",
    "Booking",
    "CaptureLeadArgs",
    "CheckAvailabilityArgs",
    "DemoCalendar",
    "DemoCrm",
    "Lead",
    "build_appointment_runtime",
]
