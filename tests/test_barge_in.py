"""Barge-in tests.

WHAT THESE PROVE
  1. The interruption/turn-detection configuration this project ships is the one
     that reaches AgentSession, and AgentSession accepts it (no silent drop of a
     misspelled key).
  2. LiveKit's interruption *control path* works end to end against a real
     AgentSession: an in-flight utterance stops, the SpeechHandle is marked
     interrupted, the audio sink is told to drop its buffer, and playout stops
     early rather than draining to the end.
  3. Speech marked uninterruptible — which is what a mutating tool call runs
     under — survives a normal interrupt and only yields to a forced one.

WHAT THESE DO NOT PROVE
  Nothing here involves a microphone, real speech, Silero VAD scoring, Deepgram
  transcription, or WebRTC. The trigger is a direct `session.interrupt()` call,
  not detected user speech. Whether `min_duration=0.4` / `min_words=2` are the
  right thresholds against a real caller on a real speakerphone is an acoustic
  question these tests cannot answer. That is the manual browser acceptance
  script in the README, and it has not been run — no credentials. See
  README "Blocked / not verified".
"""

from __future__ import annotations

import asyncio

import pytest
from livekit.agents import Agent, AgentSession

from voice_agent.config import Settings
from voice_agent.turn_handling import build_silero_vad_kwargs, build_turn_handling

from .fakes import PacedAudioOutput, SilenceTTS

UTTERANCE = "I have ten in the morning or three in the afternoon on Tuesday, which suits you"
SPEECH_SECONDS = 4.0


# --------------------------------------------------------------------------
# 1. Configuration
# --------------------------------------------------------------------------


def test_interruption_is_enabled_and_explicitly_bounded() -> None:
    cfg = build_turn_handling(Settings())
    interruption = cfg["interruption"]

    assert interruption["enabled"] is True
    assert interruption["min_duration"] == 0.4
    assert interruption["min_words"] == 2
    assert interruption["discard_audio_if_uninterruptible"] is True
    assert interruption["resume_false_interruption"] is True
    assert interruption["false_interruption_timeout"] == 2.0


def test_turn_detection_and_endpointing_are_set_explicitly() -> None:
    cfg = build_turn_handling(Settings())

    assert cfg["turn_detection"] == "vad"
    assert cfg["endpointing"]["mode"] == "fixed"
    assert cfg["endpointing"]["min_delay"] == 0.4
    assert cfg["endpointing"]["max_delay"] == 3.0


def test_turn_handling_is_tunable_from_the_environment() -> None:
    settings = Settings.from_env(
        {"INTERRUPTION_MIN_DURATION": "0.25", "INTERRUPTION_MIN_WORDS": "1"}
    )
    cfg = build_turn_handling(settings)

    assert cfg["interruption"]["min_duration"] == 0.25
    assert cfg["interruption"]["min_words"] == 1


def test_silero_vad_tuning_is_explicit() -> None:
    kwargs = build_silero_vad_kwargs()

    assert kwargs["min_silence_duration"] == 0.4
    assert kwargs["prefix_padding_duration"] == 0.5
    assert kwargs["min_speech_duration"] == 0.05


@pytest.mark.asyncio
async def test_agent_session_accepts_this_turn_handling_config() -> None:
    """Guards against the failure that costs an afternoon: a key that LiveKit
    ignores, so the setting silently never applies."""
    async with AgentSession(
        tts=SilenceTTS(seconds=0.2), turn_handling=build_turn_handling(Settings())
    ) as session:
        assert session is not None


# --------------------------------------------------------------------------
# 2. Interruption control path, against a real AgentSession
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_interrupt_stops_speech_mid_utterance() -> None:
    output = PacedAudioOutput()

    async with AgentSession(
        tts=SilenceTTS(seconds=SPEECH_SECONDS),
        turn_handling=build_turn_handling(Settings()),
    ) as session:
        session.output.audio = output
        await session.start(Agent(instructions="test"))

        handle = session.say(UTTERANCE)
        await asyncio.sleep(1.0)

        assert not handle.done(), "utterance should still be playing out"
        assert 0.5 < output.played_seconds < SPEECH_SECONDS

        await session.interrupt()
        await asyncio.sleep(0.3)

        assert handle.interrupted is True
        assert handle.done()
        assert output.interrupted_segments == 1, "sink should be told to drop its buffer"
        assert output.played_seconds < SPEECH_SECONDS * 0.6, (
            f"playout should stop early, played {output.played_seconds:.2f}s of {SPEECH_SECONDS}s"
        )


@pytest.mark.asyncio
async def test_uninterrupted_speech_plays_to_completion() -> None:
    """The control for the test above: without an interrupt, the same utterance
    drains fully. Otherwise 'stopped early' would prove nothing."""
    output = PacedAudioOutput()

    async with AgentSession(
        tts=SilenceTTS(seconds=1.0), turn_handling=build_turn_handling(Settings())
    ) as session:
        session.output.audio = output
        await session.start(Agent(instructions="test"))

        handle = session.say("short line")
        await handle.wait_for_playout()

        assert handle.interrupted is False
        assert output.interrupted_segments == 0
        assert output.played_seconds >= 0.9


@pytest.mark.asyncio
async def test_uninterruptible_speech_survives_a_normal_interrupt() -> None:
    """This is the guarantee `book_appointment` leans on via
    `context.disallow_interruptions()`: a write in progress is not abandoned
    halfway because the caller made a noise.

    LiveKit refuses the interrupt loudly rather than silently ignoring it —
    `SpeechHandle.interrupt()` raises unless `force=True`. Asserting on the raise
    pins that behaviour: if a future version downgraded it to a silent no-op the
    end state would look identical, and this test would still catch the change.
    """
    output = PacedAudioOutput()

    async with AgentSession(
        tts=SilenceTTS(seconds=1.5), turn_handling=build_turn_handling(Settings())
    ) as session:
        session.output.audio = output
        await session.start(Agent(instructions="test"))

        handle = session.say("booking you in now", allow_interruptions=False)
        await asyncio.sleep(0.4)

        with pytest.raises(RuntimeError, match="does not allow interruptions"):
            session.interrupt()

        assert handle.interrupted is False, "uninterruptible speech must not yield"

        await handle.wait_for_playout()
        assert handle.interrupted is False
        assert output.interrupted_segments == 0
        assert output.played_seconds >= 1.4, "the full utterance should have played"


@pytest.mark.asyncio
async def test_forced_interrupt_overrides_uninterruptible_speech() -> None:
    """The escape hatch: a caller hanging up or a hard stop must still cut audio."""
    output = PacedAudioOutput()

    async with AgentSession(
        tts=SilenceTTS(seconds=SPEECH_SECONDS),
        turn_handling=build_turn_handling(Settings()),
    ) as session:
        session.output.audio = output
        await session.start(Agent(instructions="test"))

        handle = session.say("booking you in now", allow_interruptions=False)
        await asyncio.sleep(0.5)
        await session.interrupt(force=True)
        await asyncio.sleep(0.3)

        assert handle.interrupted is True
        assert output.played_seconds < SPEECH_SECONDS * 0.6
