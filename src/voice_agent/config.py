"""Environment configuration.

Deliberate choice: this module never stores a secret value. It records only
whether each credential is *present*. The LiveKit plugins read their own API
keys straight from the environment, so there is no reason for a settings object
to hold one — and an object that never holds a secret cannot leak one through a
repr, a log line, or a crash dump.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

# Credentials the agent needs to run against a real room. Names only.
REQUIRED_CREDENTIAL_VARS: tuple[str, ...] = (
    "LIVEKIT_URL",
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
    "DEEPGRAM_API_KEY",
    "OPENAI_API_KEY",
)


def _float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    """Non-secret configuration, plus which credentials were found."""

    # Models. Overridable so a client swap is an env change, not a code change.
    stt_model: str = "nova-3"
    stt_language: str = "en-US"
    llm_model: str = "gpt-4.1-mini"
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "ash"

    # Turn-taking / barge-in. See README "Barge-in findings".
    interruption_min_duration: float = 0.4
    interruption_min_words: int = 2
    false_interruption_timeout: float = 2.0
    endpointing_min_delay: float = 0.4
    endpointing_max_delay: float = 3.0

    # Tool-runtime bounds (agency limits).
    tool_max_attempts: int = 3
    tool_backoff_base_seconds: float = 0.2
    tool_backoff_max_seconds: float = 2.0
    tool_timeout_seconds: float = 5.0

    present_credentials: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source: Mapping[str, str] = os.environ if env is None else env
        present = frozenset(
            name
            for name in REQUIRED_CREDENTIAL_VARS
            if (source.get(name) or "").strip() not in ("", "REPLACE_ME")
        )
        settings = cls(
            stt_model=source.get("DEEPGRAM_STT_MODEL") or "nova-3",
            stt_language=source.get("DEEPGRAM_STT_LANGUAGE") or "en-US",
            llm_model=source.get("OPENAI_LLM_MODEL") or "gpt-4.1-mini",
            tts_model=source.get("OPENAI_TTS_MODEL") or "gpt-4o-mini-tts",
            tts_voice=source.get("OPENAI_TTS_VOICE") or "ash",
            interruption_min_duration=_float(source, "INTERRUPTION_MIN_DURATION", 0.4),
            interruption_min_words=_int(source, "INTERRUPTION_MIN_WORDS", 2),
            false_interruption_timeout=_float(source, "FALSE_INTERRUPTION_TIMEOUT", 2.0),
            endpointing_min_delay=_float(source, "ENDPOINTING_MIN_DELAY", 0.4),
            endpointing_max_delay=_float(source, "ENDPOINTING_MAX_DELAY", 3.0),
            tool_max_attempts=_int(source, "TOOL_MAX_ATTEMPTS", 3),
            tool_backoff_base_seconds=_float(source, "TOOL_BACKOFF_BASE_SECONDS", 0.2),
            tool_backoff_max_seconds=_float(source, "TOOL_BACKOFF_MAX_SECONDS", 2.0),
            tool_timeout_seconds=_float(source, "TOOL_TIMEOUT_SECONDS", 5.0),
            present_credentials=present,
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.interruption_min_duration <= 0:
            raise ValueError("INTERRUPTION_MIN_DURATION must be > 0")
        if self.interruption_min_words < 0:
            raise ValueError("INTERRUPTION_MIN_WORDS must be >= 0")
        if self.endpointing_min_delay <= 0:
            raise ValueError("ENDPOINTING_MIN_DELAY must be > 0")
        if self.endpointing_max_delay < self.endpointing_min_delay:
            raise ValueError("ENDPOINTING_MAX_DELAY must be >= ENDPOINTING_MIN_DELAY")

    @property
    def missing_credentials(self) -> tuple[str, ...]:
        return tuple(n for n in REQUIRED_CREDENTIAL_VARS if n not in self.present_credentials)

    @property
    def is_runnable(self) -> bool:
        """True when every credential needed for a live room is present."""
        return not self.missing_credentials

    def describe(self) -> dict[str, object]:
        """A log-safe summary. Credential *names* only, never values."""
        return {
            "stt": f"deepgram/{self.stt_model}",
            "llm": f"openai/{self.llm_model}",
            "tts": f"openai/{self.tts_model} ({self.tts_voice})",
            "vad": "silero",
            "interruption_min_duration": self.interruption_min_duration,
            "interruption_min_words": self.interruption_min_words,
            "false_interruption_timeout": self.false_interruption_timeout,
            "endpointing": [self.endpointing_min_delay, self.endpointing_max_delay],
            "tool_max_attempts": self.tool_max_attempts,
            "credentials_present": sorted(self.present_credentials),
            "credentials_missing": list(self.missing_credentials),
        }
