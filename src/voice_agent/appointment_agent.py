"""The LiveKit binding: an ``Agent`` whose tools are thin wrappers over the runtime.

Everything below is adapter code. It converts a LiveKit tool call into a
``ToolRuntime.invoke`` and converts the outcome back into either a value for the
LLM or a ``ToolError``. No validation, retry, keying or logging lives here — if
it did, a second project reusing the runtime would have to re-implement it.

Sources (LiveKit Agents 1.6.7):
  https://docs.livekit.io/agents/logic/tools/definition.md
  https://docs.livekit.io/agents/logic/tools/definition.md#error-handling
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from livekit.agents import Agent, RunContext, function_tool
from livekit.agents.llm import ToolError

from .appointments import DemoCalendar, DemoCrm, build_appointment_runtime
from .config import Settings
from .readback import ReadbackMismatch
from .readback import verify as verify_readback
from .tool_runtime import RetryPolicy, ToolOutcome, ToolRuntime

logger = logging.getLogger("voice_agent.agent")

BASE_INSTRUCTIONS = """\
You are the receptionist for a small service business. You answer inbound calls,
check appointment availability, book appointments, and take a message when the
caller is not ready to book.

How to speak:
- One or two sentences, then stop and let the caller talk.
- Plain spoken language. No lists, no markdown, no emoji, no special characters.
- If the caller talks over you, stop immediately and listen. Never talk through
  an interruption.
- If asked whether you are an AI, say yes right away, without hedging.

How to work:
- Call check_availability before offering any time. Never invent a slot.
- Offer at most two times at once.
- Before booking, read the caller's phone number back to them digit by digit and
  get an explicit yes. A wrong number makes the booking worthless.
- Call book_appointment exactly once per confirmed appointment. If you are unsure
  whether a booking went through, say so and ask, rather than calling it again.
- If the caller will not book now, use capture_lead so someone can follow up.
- If a tool reports an error, tell the caller plainly what happened and offer an
  alternative. Do not guess at a result you did not get.

What you may confirm:
- Only confirm what a tool actually recorded. When you read a booking back, state
  the time and nothing else you did not send to book_appointment.
- The same rule governs what you say about availability. Describe exactly the times
  check_availability returned. Do not say a time is the only one, the last one, or
  the closest one unless the tool result actually shows that, and if the caller asks
  about a part of the day, name every slot the tool returned in that range.
- If the caller asks for something the booking cannot record — a specific person,
  a room, an accommodation, a preference of any kind — do not fold it into your
  confirmation. Say plainly that you cannot guarantee it on this call and that you
  will pass it along, then carry on.
- Never say an appointment is "set for" or "arranged with" a detail you did not
  send to a tool. A caller who hangs up believing something you never recorded is
  worse than a caller you told no.

Never quote prices and never promise a timeline.
"""


# The availability clause was added after run 4 (2026-07-28): with 09:00, 10:00, 13:00
# and 15:00 all open, the agent told a caller "there is only a slot at three PM in the
# afternoon." It did not invent a slot — that guard has held five runs — but it asserted
# an exclusivity the tool result contradicted, which loses a booking just as surely.
# Same overclaim family as the confirmation bug, and the original wording did not reach
# it because it governed confirmations rather than descriptions.
#
# The "What you may confirm" block above exists because of run 3 (2026-07-28). A caller
# asked for a female doctor; the agent acknowledged it, booked, and then confirmed the
# appointment was "set for 1 PM tomorrow with a female doctor." Nothing had recorded that
# preference — BookAppointmentArgs has extra="forbid", so the model could not have passed
# it even if it had tried. The schema held. The spoken confirmation did not, and that is
# the more dangerous half: the date-anchor bug below failed visibly, this one fails
# silently. See docs/acceptance-findings.md, run 3, defect 1.

# On the first live call (2026-07-27) the model was asked for "tomorrow" and called
# check_availability with 2024-06-03, then 2024-06-05 — dates from its training data.
# The calendar guard rejected both ("that date is in the past") so nothing was
# hallucinated, but the agent could not book anything at all: with no anchor, EVERY
# relative date a real caller uses is unresolvable. An LLM has no clock. If you do not
# tell it what day it is, it will confidently pick one.
_DATE_ANCHOR = """\

Today's date:
- Today is {weekday}, {long_date} ({iso}).
- Resolve relative dates yourself against that. "Tomorrow" is {tomorrow_iso}.
  "Next week" means the week beginning {next_monday_iso}.
- Always pass check_availability an absolute date in YYYY-MM-DD form. Never guess a
  year, and never pass a date before {iso}.
- The calendar is open Monday to Friday only. If the caller asks for a weekend, say
  so plainly and offer the nearest weekday instead.
"""


def build_instructions(today: date) -> str:
    """The system prompt, anchored to a specific date.

    Args:
        today: The date the agent should treat as "today". Pass the same value the
            calendar uses, so the prompt and the past-date guard can never disagree.
    """
    from datetime import timedelta

    days_to_monday = (7 - today.weekday()) % 7 or 7
    return BASE_INSTRUCTIONS + _DATE_ANCHOR.format(
        weekday=today.strftime("%A"),
        long_date=today.strftime("%B %-d, %Y"),
        iso=today.isoformat(),
        tomorrow_iso=(today + timedelta(days=1)).isoformat(),
        next_monday_iso=(today + timedelta(days=days_to_monday)).isoformat(),
    )


class AppointmentAgent(Agent):
    """Inbound appointment-booking agent.

    Args:
        runtime: A configured :class:`ToolRuntime`. Injected rather than built
            here so tests can pass one with fake adapters and a captured log.
        today: The date to anchor the prompt to. Defaults to the host's real date.
            Pass the calendar's ``today`` so the two cannot drift apart.
    """

    def __init__(self, *, runtime: ToolRuntime, today: date | None = None) -> None:
        self._today = today or date.today()
        super().__init__(instructions=build_instructions(self._today))
        self._runtime = runtime

    @property
    def today(self) -> date:
        """The date this agent's prompt is anchored to."""
        return self._today

    # ---- adapter plumbing --------------------------------------------

    @staticmethod
    def _turn_id(context: RunContext[None]) -> str:
        """The call-turn identity used for idempotency.

        ``speech_handle.id`` is stable for one agent turn, so two tool calls the
        model emits while producing a single reply share it. That is exactly the
        double-fire window this guards. ``function_call.call_id`` would be unique
        per emission and would therefore guard nothing.
        """
        handle = getattr(context, "speech_handle", None)
        handle_id = getattr(handle, "id", None)
        if handle_id:
            return str(handle_id)
        call = getattr(context, "function_call", None)
        return str(getattr(call, "call_id", "unknown-turn"))

    async def _invoke(
        self, context: RunContext[None], tool: str, raw_args: dict[str, Any]
    ) -> dict[str, Any]:
        outcome: ToolOutcome[Any] = await self._runtime.invoke(
            tool, turn_id=self._turn_id(context), raw_args=raw_args
        )
        if outcome.status == "invalid":
            # Surfaced to the LLM so it can re-ask the caller. The message names
            # fields only; it never echoes what the caller or the model supplied.
            raise ToolError(outcome.error or "invalid arguments")
        if outcome.status == "failed":
            raise ToolError(outcome.error or "the request could not be completed")
        result = outcome.result if isinstance(outcome.result, dict) else {"result": outcome.result}
        # "replayed" is deliberately not surfaced as an error: the caller asked
        # once, so they hear one confirmation, and the calendar saw one write.
        return {**result, "already_recorded": outcome.status == "replayed"}

    def _recent_assistant_messages(self) -> list[str]:
        """The agent's own recent utterances, oldest first.

        Read defensively: ``chat_ctx`` is a framework structure and this runs on the
        path to a write, so a shape change upstream must not take the booking down
        with it. An empty list makes the read-back check fail closed, which is the
        safe direction.
        """
        try:
            items = list(getattr(self.chat_ctx, "items", []) or [])
        except Exception:  # pragma: no cover - defensive
            return []
        out: list[str] = []
        for item in items:
            if getattr(item, "role", None) != "assistant":
                continue
            text = getattr(item, "text_content", None) or getattr(item, "raw_text_content", None)
            if isinstance(text, str) and text.strip():
                out.append(text)
        return out

    # ---- tools --------------------------------------------------------

    @function_tool()
    async def check_availability(self, context: RunContext[None], date: str) -> dict[str, Any]:
        """Look up open appointment times on one day.

        Call this before offering the caller any time. Never invent a time.

        Args:
            date: The day to check, as YYYY-MM-DD.
        """
        return await self._invoke(context, "check_availability", {"date": date})

    @function_tool()
    async def book_appointment(
        self,
        context: RunContext[None],
        slot_id: str,
        caller_name: str,
        phone: str,
        email: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Reserve one appointment time for this caller.

        Only call this after the caller has confirmed both the time and their
        phone number. Call it once per appointment.

        Args:
            slot_id: A slot exactly as returned by check_availability.
            caller_name: The caller's full name.
            phone: The caller's callback number, digits as they said them.
            email: The caller's email, if they offered one.
            reason: One short phrase describing what the appointment is for.
        """
        # This tool mutates state. Letting a barge-in cancel it mid-write is how
        # you get a booking that exists on the calendar but was never confirmed
        # out loud. Source: docs.livekit.io/agents/logic/tools/definition.md
        # ("Disable interruptions for mutating calls").
        context.disallow_interruptions()

        # Structural read-back check. Run 5 session 1 read back ten digits and wrote
        # eleven, having bundled the confirmation with a second question so the caller
        # never agreed to the number at all. The instruction to read it back was already
        # there; this is the part that does not depend on the model following it.
        # See readback.py and docs/acceptance-findings.md, run 5, defect 6.
        try:
            verify_readback(phone, self._recent_assistant_messages())
        except ReadbackMismatch as exc:
            raise ToolError(str(exc)) from exc
        return await self._invoke(
            context,
            "book_appointment",
            {
                "slot_id": slot_id,
                "caller_name": caller_name,
                "phone": phone,
                "email": email,
                "reason": reason,
            },
        )

    @function_tool()
    async def capture_lead(
        self,
        context: RunContext[None],
        caller_name: str,
        phone: str,
        email: str | None = None,
        interest: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Record a caller who is not booking right now, so someone can follow up.

        Args:
            caller_name: The caller's full name.
            phone: The caller's callback number.
            email: The caller's email, if they offered one.
            interest: What they were asking about, in one short phrase.
            notes: Anything else worth passing along.
        """
        context.disallow_interruptions()
        return await self._invoke(
            context,
            "capture_lead",
            {
                "caller_name": caller_name,
                "phone": phone,
                "email": email,
                "interest": interest,
                "notes": notes,
            },
        )


def build_agent(settings: Settings) -> AppointmentAgent:
    """Construct the agent with demo adapters and settings-derived bounds."""
    calendar = DemoCalendar()
    runtime = build_appointment_runtime(
        calendar=calendar,
        crm=DemoCrm(),
        retry=RetryPolicy(
            max_attempts=settings.tool_max_attempts,
            base_delay=settings.tool_backoff_base_seconds,
            max_delay=settings.tool_backoff_max_seconds,
            timeout=settings.tool_timeout_seconds,
        ),
    )
    # Same date object drives the prompt and the past-date guard.
    return AppointmentAgent(runtime=runtime, today=calendar.today)
