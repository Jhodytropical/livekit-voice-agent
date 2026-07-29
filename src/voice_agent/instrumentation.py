"""Barge-in instrumentation that can tell an interruption from a coincidence.

Why this module exists, in three versions of the same measurement:

**v1 (2026-07-27).** Latency was inferred from log ordering. It could not work:
the only visible signal was an assistant ``conversation_item_added`` line with
truncated text, and that line is emitted as bookkeeping when the *user's* turn
commits, so it lands 2-5 ms after the final transcript every time regardless of
when audio stopped. Three different interruption configs produced the same 2-5 ms
offset — the giveaway that we were measuring the logger, not the agent.

**v2 (2026-07-27).** Replaced with an event pair: ``user_state`` flips to
"speaking" on VAD onset, ``agent_state`` leaves "speaking" when playout stops.
The difference is real, and nothing in the pipeline sits between them. The rule
for deciding whether a stop counted was *only a stop that follows a user onset is
a barge-in*, which is necessary but not sufficient — and that gap is what v3 fixes.

**v3 (2026-07-28).** A cross-check of all 17 samples logged under v2 found that only
4 showed the agent's speech actually truncated. The other 13 were the agent finishing
its sentence as the caller happened to start talking. v2 had no way to tell those
apart, so it labelled every one a barge-in — including both 349 ms samples, which
were the fastest numbers on record and went into the README.

v3 stopped inferring and asked ``session.current_speech.interrupted`` instead. It
was wrong in the opposite direction, and run 5 caught it within one call: **6
assistant utterances were cut off mid-sentence and only 2 were reported as
interruptions.** ``current_speech`` is not reliably the handle that just stopped —
preemptive generation queues the next speech while the current one is still playing,
and the agent state does not dip to "listening" between two queued utterances, so the
handle captured when speaking began can belong to a different utterance by the time it
ends.

**v4 (2026-07-28).** Use the per-utterance record instead of a mutable session pointer.
``agent_activity.py`` builds each assistant ``ChatMessage`` with
``interrupted=speech_handle.interrupted`` from the exact handle that produced it. v4 read
that flag off the ``conversation_item_added`` event, assuming the item always arrives just
before the stop.

It does on one path and not on the other. From run 5's second session, live on v4::

    16:12:42  assistant item (truncated)  ->  stop: interrupted=True    correct
    16:12:25  stop: interrupted=False     ->  assistant item (truncated)  MISSED

The generation path emits the item first (``agent_activity.py:2836-2839``); the
``generate_reply`` path used for the greeting emits it after. When the stop arrived first
there was no item to read, so v4 fell back to the stale handle and inherited v3's bug.

**v5 (2026-07-28, this module).** Stop assuming an order. The stop event carries the
*timing*, the item carries the *verdict*; each is stashed as half a record, and the record
is emitted once both halves are in hand, whichever lands first.

And because three of five versions of this measurement have been wrong, v5 logs the
independent tell beside the verdict. ``text_truncated`` — did the assistant's own text
stop mid-sentence — is exactly the cross-check that caught v2, v3 and v4, all three times
by hand and after the fact. It now ships in the record, and a record whose two signals
disagree is flagged ``signals_disagree``.

The lesson worth keeping: **a measurement that only agrees with itself is not evidence.**
Every version here looked correct until it was checked against something outside it. The
outside check now lives inside the log.

The overlap measurement is not discarded when that check fails — it is real data
about how often a caller starts talking as the agent lands. It moves to
``user_overlap_ms`` so it can never again be read as a latency figure.

This module also listens for ``agent_false_interruption``. Steps 5 of the manual
script — cough / non-speech rejection — was scored "not observed" across four
runs. The event fires (run 3's log carries ``resumed false interrupted speech``);
nothing was listening for it. It was never unobservable, only unobserved.

See ``docs/acceptance-findings.md``, run 4, defect 5.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Protocol

logger = logging.getLogger("voice_agent")

#: Overlaps shorter than this are physically implausible as playout-stop latency
#: (9.9 ms and 67.4 ms have both been logged). They are recorded but flagged, so a
#: future reader does not have to rediscover why the minimum looks impossible.
IMPLAUSIBLE_OVERLAP_MS = 100.0


class _Session(Protocol):
    """The slice of ``AgentSession`` this module uses."""

    def on(self, event: str, callback: Any = None) -> Any: ...


def instrument_barge_in(session: _Session, *, emit: Any = None) -> None:
    """Attach barge-in instrumentation to a session.

    Args:
        session: The ``AgentSession`` to observe.
        emit: Optional sink taking one dict, for tests. Defaults to a JSON log line.
    """
    sink = emit if emit is not None else _log

    marks: dict[str, Any] = {}
    pending_stop: dict[str, Any] = {}
    pending_item: dict[str, Any] = {}

    def _flush() -> None:
        """Emit once both halves of a record are in hand.

        The stop event knows *when*; the assistant item knows *whether*. They arrive in
        either order depending on which code path produced the speech, so neither may
        assume it is second. That assumption is precisely what broke v4.
        """
        if not pending_stop or not pending_item:
            return
        stop, item = dict(pending_stop), dict(pending_item)
        pending_stop.clear()
        pending_item.clear()

        interrupted = item["interrupted"]
        overlap = stop["overlap_ms"]
        truncated = item["truncated"]

        # A barge-in is counted only when both signals agree: the framework issued an
        # interrupt AND words were actually lost. Run 5 session 3 is why. Two stops
        # there had `interrupted=True` with the agent's sentence fully delivered — the
        # caller answered on the final word. Calling that a 399.7 ms barge-in would
        # advertise "the agent yields fast when you talk over it" on an event where
        # nothing was cut off. `interrupt_issued` keeps that looser count, named for
        # what it is.
        confirmed = interrupted is True and truncated is True

        record: dict[str, Any] = {
            "event": "agent_stopped_speaking",
            "timestamp": stop["timestamp"],
            "to_state": stop["to_state"],
            "playout_interrupted": interrupted,
            # The independent tell, shipped in the record because checking it by hand
            # after the fact is what caught all three previous versions.
            "text_truncated": truncated,
            "interrupt_issued": interrupted,
            # The quotable figure. Non-null only when both signals agree.
            "barge_in_latency_ms": overlap if confirmed else None,
            "user_overlap_ms": None if confirmed else overlap,
        }
        if truncated is not None and interrupted is not None and truncated != interrupted:
            record["signals_disagree"] = True
        if confirmed and overlap is not None and overlap < IMPLAUSIBLE_OVERLAP_MS:
            record["implausible"] = True
        sink(record)

    def _flush_unpaired() -> None:
        if not pending_stop or pending_item:
            return
        stop = dict(pending_stop)
        pending_stop.clear()
        sink(
            {
                "event": "agent_stopped_speaking",
                "timestamp": stop["timestamp"],
                "to_state": stop["to_state"],
                "playout_interrupted": None,
                "text_truncated": None,
                "interrupt_issued": None,
                "barge_in_latency_ms": None,
                "user_overlap_ms": stop["overlap_ms"],
                "unpaired": True,
            }
        )

    @session.on("user_state_changed")
    def _on_user(ev: Any) -> None:
        if getattr(ev, "new_state", None) == "speaking":
            marks["user_speaking_at"] = time.time()
            sink({"event": "user_started_speaking", "timestamp": round(time.time(), 4)})

    @session.on("agent_state_changed")
    def _on_agent(ev: Any) -> None:
        old_state = getattr(ev, "old_state", None)
        new_state = getattr(ev, "new_state", None)
        now = time.time()

        if new_state == "speaking":
            # A stop still unpaired when the next utterance begins will never be paired.
            # Emit it as unknown rather than dropping it: silent loss is the failure mode
            # this whole module exists to stop repeating.
            _flush_unpaired()
            marks["agent_speaking_at"] = now
            sink({"event": "agent_started_speaking", "timestamp": round(now, 4)})
            return

        if old_state != "speaking":
            return

        started = marks.get("user_speaking_at")
        agent_started = marks.get("agent_speaking_at", 0)
        pending_stop.update(
            timestamp=round(now, 4),
            to_state=new_state,
            overlap_ms=(
                round((now - started) * 1000, 1) if started and started > agent_started else None
            ),
        )
        _flush()

    @session.on("conversation_item_added")
    def _on_item(ev: Any) -> None:
        item = getattr(ev, "item", None)
        if getattr(item, "role", None) != "assistant":
            return
        text = getattr(item, "text_content", None) or getattr(item, "raw_text_content", None)
        pending_item.update(
            interrupted=getattr(item, "interrupted", None),
            truncated=_looks_truncated(text),
        )
        _flush()

    @session.on("agent_false_interruption")
    def _on_false(ev: Any) -> None:
        # Step 5 of the manual script. Four runs scored this "not observed" because
        # nothing was listening, not because nothing happened.
        sink(
            {
                "event": "agent_false_interruption",
                "timestamp": round(time.time(), 4),
                "resumed": getattr(ev, "resumed", None),
            }
        )


def _looks_truncated(text: Any) -> bool | None:
    """Did the utterance stop mid-sentence? ``None`` when there is nothing to judge.

    A heuristic, and labelled as one: an interruption landing exactly on a final word
    reads as complete. This is not the verdict — it is the second opinion, and its whole
    value is in disagreeing with the first.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    return not text.rstrip().endswith((".", "!", "?"))


def _log(record: dict[str, Any]) -> None:
    logger.info(json.dumps(record))
