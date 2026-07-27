"""Explicit turn-detection and interruption configuration.

Kept in its own module for one reason: it can be built and asserted on without
a LiveKit room, a model, or a credential. The barge-in configuration is the part
of a voice agent most likely to be silently wrong, so it gets to be unit-tested
rather than only observed by ear.

Sources (LiveKit Agents 1.6.7):
  https://docs.livekit.io/reference/agents/turn-handling-options.md
  https://docs.livekit.io/agents/logic/turns.md
  https://docs.livekit.io/agents/logic/turns/vad.md
"""

from __future__ import annotations

from typing import Any

from livekit.agents import TurnHandlingOptions

from .config import Settings


def build_turn_handling(settings: Settings) -> TurnHandlingOptions:
    """Return the ``turn_handling`` argument for ``AgentSession``.

    Every value is set explicitly, including ones that happen to match the
    framework default. On a receptionist call the defaults are not self-evidently
    right, and an inherited default is invisible in review.

    The return type is LiveKit's own ``TurnHandlingOptions`` TypedDict rather than
    a loose dict, so a misspelled key is a type error here instead of a setting
    that silently never applies on a live call.
    """
    return {
        # VAD-based endpointing. LiveKit's audio turn detector is the framework
        # default and is generally better at "the caller paused mid-sentence";
        # it runs through LiveKit Inference, so it is a credentialed dependency.
        # This project pins the explicit, self-contained option so the pipeline
        # is Deepgram + OpenAI + Silero and nothing else. See README for the swap.
        "turn_detection": "vad",
        "endpointing": {
            "mode": "fixed",
            # A receptionist call is full of short clipped answers ("yeah",
            # "ten's fine"). Waiting the 0.5s default before accepting the turn
            # reads as sluggish; 0.4s is the floor before genuine mid-sentence
            # pauses start getting cut off.
            "min_delay": settings.endpointing_min_delay,
            "max_delay": settings.endpointing_max_delay,
        },
        "interruption": {
            "enabled": True,
            # "adaptive" needs a turn-detector model plus aligned-transcript STT.
            # With VAD-only turn detection the framework would fall back to "vad"
            # anyway; saying so explicitly beats relying on a fallback.
            "mode": "vad",
            # Drop buffered audio captured while the agent could not be
            # interrupted, so the agent does not replay a stale chunk after
            # yielding.
            "discard_audio_if_uninterruptible": True,
            # Guards against a cough, a door, a "mm-hm". Below ~0.3s the agent
            # yields to breathing noise; above ~0.6s a real interruption feels
            # ignored for an audible beat.
            "min_duration": settings.interruption_min_duration,
            # The second guard, and the one that matters on speakerphone: require
            # actual transcribed words, not just energy. This is the direct
            # counterpart to the Vapi `numWordsToInterruptAssistant` setting.
            "min_words": settings.interruption_min_words,
            # If the "interruption" produced no transcript, treat it as a false
            # positive and resume rather than leaving the caller in silence.
            "false_interruption_timeout": settings.false_interruption_timeout,
            "resume_false_interruption": True,
        },
    }


def build_silero_vad_kwargs() -> dict[str, Any]:
    """Keyword arguments for ``silero.VAD.load()``.

    Separated from the plugin call so the tuning is assertable without loading
    the ONNX model.

    Source: https://docs.livekit.io/agents/logic/turns/vad.md
    """
    return {
        # Below ~0.05s, plosives and line noise register as speech.
        "min_speech_duration": 0.05,
        # How long silence must last before VAD calls end-of-speech. The plugin
        # default (0.55s) is tuned for dictation; 0.4s suits a booking call where
        # answers are short, and it pairs with endpointing min_delay above.
        "min_silence_duration": 0.4,
        # Keep the audio just before speech was detected, so the first syllable
        # of an interruption is not clipped off the transcript.
        "prefix_padding_duration": 0.5,
        "activation_threshold": 0.5,
    }
