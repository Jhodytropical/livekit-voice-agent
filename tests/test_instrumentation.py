"""A stop is only a barge-in when playout was actually cut off.

Regression tests for run 4, defect 5. Under the previous instrumentation, 13 of
17 logged "barge-ins" were the agent finishing its own sentence while the caller
happened to start talking. These drive a real ``AgentSession`` through the
offline harness, so the interruption is LiveKit's own control path rather than a
fake event.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from livekit.agents import Agent, AgentSession

from voice_agent.instrumentation import IMPLAUSIBLE_OVERLAP_MS, instrument_barge_in

from .fakes import PacedAudioOutput, SilenceTTS

UTTERANCE = "I have ten in the morning or three in the afternoon, which of those suits you"
SPEECH_SECONDS = 4.0


def _stops(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in records if r["event"] == "agent_stopped_speaking"]


# --------------------------------------------------------------------------
# 1. Is the signal trustworthy?
#
# The whole fix rests on SpeechHandle.interrupted being the truth about whether
# playout was cut off. These drive a real AgentSession through LiveKit's own
# interruption control path and check that it is.
#
# They assert on the handle rather than on emitted records for a reason worth
# recording: offline, `agent_state_changed` only ever reaches "listening". The
# agent state machine needs room IO to enter "speaking", so no amount of
# `session.say()` here produces an agent_stopped_speaking event. The mapping from
# signal to log record is covered by section 2 instead.
# --------------------------------------------------------------------------


async def _say(*, interrupt: bool) -> Any:
    session: AgentSession[None] = AgentSession(tts=SilenceTTS(seconds=SPEECH_SECONDS))
    session.output.audio = PacedAudioOutput()
    await session.start(Agent(instructions="test"))
    try:
        handle = session.say(UTTERANCE)
        await asyncio.sleep(SPEECH_SECONDS / 2)
        if interrupt:
            session.interrupt()
        await handle
        return handle
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_handle_reports_interrupted_when_playout_is_cut_off() -> None:
    assert (await _say(interrupt=True)).interrupted is True


@pytest.mark.asyncio
async def test_handle_reports_not_interrupted_when_playout_completes() -> None:
    """The 13-of-17 case: the agent finished, so nothing was interrupted."""
    assert (await _say(interrupt=False)).interrupted is False


# --------------------------------------------------------------------------
# 2. Does the instrumentation map that signal correctly?
# --------------------------------------------------------------------------


def _emit(
    session: Any,
    *,
    user_speaks: bool = True,
    item: bool | None = None,
    text: str = "Have a good day.",
    verdictless_item: bool = False,
) -> list[dict[str, Any]]:
    """Drive one speak/stop cycle. ``item`` is the assistant message's own
    ``interrupted`` flag, which is what the framework emits just before the stop."""
    records: list[dict[str, Any]] = []
    instrument_barge_in(session, emit=records.append)
    session.fire("agent_state_changed", _Ev(old_state="listening", new_state="speaking"))
    if user_speaks:
        session.fire("user_state_changed", _Ev(new_state="speaking"))
    if item is not None or verdictless_item:
        session.fire(
            "conversation_item_added",
            _Ev(item=_Item(role="assistant", interrupted=item, text=text)),
        )
    session.fire("agent_state_changed", _Ev(old_state="speaking", new_state="listening"))
    return records


def test_the_item_may_arrive_after_the_stop() -> None:
    """The v4 bug. `generate_reply` — the greeting path — emits the item *after* the
    state change, and v4 assumed it always came first, so it missed those entirely."""
    session = _FakeSession(speech=_Handle(interrupted=False))
    records: list[dict[str, Any]] = []
    instrument_barge_in(session, emit=records.append)

    session.fire("agent_state_changed", _Ev(old_state="listening", new_state="speaking"))
    session.fire("user_state_changed", _Ev(new_state="speaking"))
    session.fire("agent_state_changed", _Ev(old_state="speaking", new_state="listening"))
    assert not _stops(records), "nothing may be emitted before the verdict is known"

    session.fire(
        "conversation_item_added",
        _Ev(item=_Item(role="assistant", interrupted=True, text="feel free to")),
    )

    stop = _stops(records)[-1]
    assert stop["playout_interrupted"] is True
    assert stop["barge_in_latency_ms"] is not None


def test_the_independent_tell_ships_in_the_record() -> None:
    stop = _stops(_emit(_FakeSession(), item=True, text="Could you please tell me"))[-1]
    assert stop["text_truncated"] is True

    stop = _stops(_emit(_FakeSession(), item=True, text="Have a good day."))[-1]
    assert stop["text_truncated"] is False


def test_disagreeing_signals_are_flagged_not_silently_reconciled() -> None:
    """Every previous version was caught by this comparison, done by hand. Now the log
    does it."""
    stop = _stops(_emit(_FakeSession(), item=False, text="Could you please tell me"))[-1]

    assert stop["playout_interrupted"] is False
    assert stop["text_truncated"] is True
    assert stop["signals_disagree"] is True


def test_agreeing_signals_are_not_flagged() -> None:
    stop = _stops(_emit(_FakeSession(), item=True, text="Could you please tell me"))[-1]
    assert "signals_disagree" not in stop


def test_an_interrupt_on_a_finished_sentence_is_not_a_quotable_barge_in() -> None:
    """Run 5 session 3, twice: the caller answered on the agent's final word. An
    interrupt was issued, nothing was cut off. Counting that as latency would
    advertise a responsiveness figure for an event where the agent lost no words."""
    stop = _stops(_emit(_FakeSession(), item=True, text="Would you like one of these?"))[-1]

    assert stop["interrupt_issued"] is True
    assert stop["text_truncated"] is False
    assert stop["barge_in_latency_ms"] is None, "an unconfirmed interrupt became a figure"
    assert stop["signals_disagree"] is True


def test_the_verdict_comes_from_the_utterance_not_the_session() -> None:
    """The v3 bug, exactly.

    Run 5 cut off 6 assistant utterances and reported 2. `current_speech` had already
    advanced to the next queued speech — preemptive generation creates it while the
    current one is still playing — so the stale handle said "not interrupted" for an
    utterance that plainly was. The item's own flag is bound to the utterance and wins.
    """
    session = _FakeSession(speech=_Handle(interrupted=False))  # stale: says not interrupted
    stop = _stops(_emit(session, item=True, text="Could you please tell me"))[-1]

    assert stop["playout_interrupted"] is True
    assert stop["barge_in_latency_ms"] is not None


def test_a_completed_utterance_is_still_not_a_barge_in_when_the_handle_is_stale() -> None:
    """The mirror case: a stale handle must not manufacture an interruption either."""
    session = _FakeSession(speech=_Handle(interrupted=True))
    stop = _stops(_emit(session, item=False))[-1]

    assert stop["playout_interrupted"] is False
    assert stop["barge_in_latency_ms"] is None
    assert stop["user_overlap_ms"] is not None


def test_one_item_cannot_answer_for_two_stops() -> None:
    """Popped on use, and cleared when a new utterance begins."""
    session = _FakeSession(speech=_Handle(interrupted=False))
    records: list[dict[str, Any]] = []
    instrument_barge_in(session, emit=records.append)

    for flag in (True, None):
        session.fire("agent_state_changed", _Ev(old_state="listening", new_state="speaking"))
        session.fire("user_state_changed", _Ev(new_state="speaking"))
        if flag is not None:
            session.fire(
                "conversation_item_added", _Ev(item=_Item(role="assistant", interrupted=flag))
            )
        else:
            session.fire(
                "conversation_item_added",
                _Ev(item=_Item(role="assistant", interrupted=False)),
            )
        session.fire("agent_state_changed", _Ev(old_state="speaking", new_state="listening"))

    first, second = _stops(records)
    assert first["playout_interrupted"] is True
    assert second["playout_interrupted"] is False, "the first item leaked into the second stop"


def test_user_items_are_ignored() -> None:
    session = _FakeSession(speech=_Handle(interrupted=False))
    records: list[dict[str, Any]] = []
    instrument_barge_in(session, emit=records.append)
    session.fire("agent_state_changed", _Ev(old_state="listening", new_state="speaking"))
    session.fire("conversation_item_added", _Ev(item=_Item(role="user", interrupted=True)))
    session.fire("conversation_item_added", _Ev(item=_Item(role="assistant", interrupted=False)))
    session.fire("agent_state_changed", _Ev(old_state="speaking", new_state="listening"))

    assert _stops(records)[-1]["playout_interrupted"] is False, "a user item set the verdict"


def test_a_confirmed_barge_in_reports_a_latency() -> None:
    """Both signals agree: an interrupt was issued and words were lost."""
    stop = _stops(_emit(_FakeSession(), item=True, text="Could you please tell me"))[-1]

    assert stop["playout_interrupted"] is True
    assert stop["text_truncated"] is True
    assert stop["barge_in_latency_ms"] is not None
    assert stop["user_overlap_ms"] is None


def test_a_completed_utterance_never_reports_a_latency() -> None:
    """The specific regression. Overlap existed; it was not an interruption."""
    stops = _stops(_emit(_FakeSession(), item=False))

    assert stops, "no agent_stopped_speaking record was emitted"
    for record in stops:
        assert record["playout_interrupted"] is False
        assert record["barge_in_latency_ms"] is None


def test_overlap_is_kept_under_a_name_that_cannot_be_misread() -> None:
    """A coincident stop is real data — it just is not latency."""
    stop = _stops(_emit(_FakeSession(), item=False))[-1]
    assert stop["playout_interrupted"] is False
    assert stop["barge_in_latency_ms"] is None
    assert stop["user_overlap_ms"] is not None


def test_implausible_overlaps_are_flagged() -> None:
    """9.9 ms and 67.4 ms were both logged as barge-ins. Flag that shape."""
    stop = _stops(_emit(_FakeSession(), item=True, text="Could you please tell me"))[-1]
    assert stop["playout_interrupted"] is True
    assert stop["barge_in_latency_ms"] < IMPLAUSIBLE_OVERLAP_MS
    assert stop["implausible"] is True


def test_an_item_with_no_verdict_reports_unknown_not_a_guess() -> None:
    stop = _stops(_emit(_FakeSession(), item=None, verdictless_item=True))[-1]

    assert stop["playout_interrupted"] is None
    assert stop["barge_in_latency_ms"] is None


def test_a_stop_that_never_pairs_is_emitted_not_dropped() -> None:
    """Silent loss is the failure mode this module exists to stop repeating."""
    session = _FakeSession()
    records: list[dict[str, Any]] = []
    instrument_barge_in(session, emit=records.append)

    session.fire("agent_state_changed", _Ev(old_state="listening", new_state="speaking"))
    session.fire("agent_state_changed", _Ev(old_state="speaking", new_state="listening"))
    session.fire("agent_state_changed", _Ev(old_state="listening", new_state="speaking"))

    stop = _stops(records)[-1]
    assert stop["unpaired"] is True
    assert stop["playout_interrupted"] is None


def test_false_interruption_is_logged() -> None:
    """Step 5 was 'not observed' four runs running because nobody listened."""
    records: list[dict[str, Any]] = []
    session = _FakeSession()
    instrument_barge_in(session, emit=records.append)

    session.fire("agent_false_interruption", _Ev(resumed=True))

    events = [r for r in records if r["event"] == "agent_false_interruption"]
    assert len(events) == 1
    assert events[0]["resumed"] is True


# ---- minimal doubles for the non-audio cases ------------------------------


class _Ev:
    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


class _Handle:
    def __init__(self, *, interrupted: bool) -> None:
        self.interrupted = interrupted


class _Item:
    def __init__(
        self, *, role: str, interrupted: bool | None, text: str = "Have a good day."
    ) -> None:
        self.role = role
        self.interrupted = interrupted
        self.text_content = text


_UNSET = object()


class _FakeSession:
    """``speech=None`` means the handle could not be observed, which is distinct
    from the default of an observable, uninterrupted one."""

    def __init__(self, speech: Any = _UNSET) -> None:
        self._handlers: dict[str, list[Any]] = {}
        self.current_speech = _Handle(interrupted=False) if speech is _UNSET else speech

    def on(self, event: str, callback: Any = None) -> Any:
        def register(fn: Any) -> Any:
            self._handlers.setdefault(event, []).append(fn)
            return fn

        return register(callback) if callback else register

    def fire(self, event: str, ev: Any) -> None:
        for fn in self._handlers.get(event, []):
            fn(ev)
