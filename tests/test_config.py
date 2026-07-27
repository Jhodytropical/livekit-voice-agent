"""Configuration and credential-free startup behaviour."""

from __future__ import annotations

import pytest

from voice_agent.config import REQUIRED_CREDENTIAL_VARS, Settings


def test_defaults_load_with_an_empty_environment() -> None:
    settings = Settings.from_env({})

    assert settings.llm_model == "gpt-4.1-mini"
    assert settings.stt_model == "nova-3"
    assert settings.is_runnable is False
    assert set(settings.missing_credentials) == set(REQUIRED_CREDENTIAL_VARS)


def test_placeholder_values_do_not_count_as_credentials() -> None:
    """.env.example ships REPLACE_ME everywhere. Copying it without editing must
    not look like a configured environment."""
    env = dict.fromkeys(REQUIRED_CREDENTIAL_VARS, "REPLACE_ME")
    settings = Settings.from_env(env)

    assert settings.is_runnable is False
    assert set(settings.missing_credentials) == set(REQUIRED_CREDENTIAL_VARS)


def test_all_credentials_present_makes_it_runnable() -> None:
    env = dict.fromkeys(REQUIRED_CREDENTIAL_VARS, "value")
    settings = Settings.from_env(env)

    assert settings.is_runnable is True
    assert settings.missing_credentials == ()


def test_settings_never_hold_a_credential_value() -> None:
    env = dict.fromkeys(REQUIRED_CREDENTIAL_VARS, "sk-super-secret-value")
    settings = Settings.from_env(env)

    assert "sk-super-secret-value" not in repr(settings)
    assert "sk-super-secret-value" not in repr(settings.describe())


def test_describe_reports_names_not_values() -> None:
    env = dict.fromkeys(REQUIRED_CREDENTIAL_VARS, "value")
    described = Settings.from_env(env).describe()

    assert described["credentials_present"] == sorted(REQUIRED_CREDENTIAL_VARS)
    assert described["credentials_missing"] == []
    assert described["vad"] == "silero"


def test_overrides_are_read_from_the_environment() -> None:
    settings = Settings.from_env(
        {"OPENAI_LLM_MODEL": "gpt-4.1", "TOOL_MAX_ATTEMPTS": "2", "ENDPOINTING_MIN_DELAY": "0.6"}
    )

    assert settings.llm_model == "gpt-4.1"
    assert settings.tool_max_attempts == 2
    assert settings.endpointing_min_delay == 0.6


def test_non_numeric_override_fails_loudly() -> None:
    with pytest.raises(ValueError, match="INTERRUPTION_MIN_DURATION"):
        Settings.from_env({"INTERRUPTION_MIN_DURATION": "half a second"})


def test_incoherent_endpointing_window_is_rejected() -> None:
    with pytest.raises(ValueError, match="ENDPOINTING_MAX_DELAY"):
        Settings.from_env({"ENDPOINTING_MIN_DELAY": "2.0", "ENDPOINTING_MAX_DELAY": "1.0"})


def test_agent_and_tools_build_without_any_credential() -> None:
    """The startup path up to (not including) connecting to a room must work
    offline. If it did not, every config error would only surface on a live call."""
    from voice_agent.appointment_agent import build_agent
    from voice_agent.turn_handling import build_silero_vad_kwargs, build_turn_handling

    settings = Settings.from_env({})
    agent = build_agent(settings)

    assert sorted(t.id for t in agent.tools) == [
        "book_appointment",
        "capture_lead",
        "check_availability",
    ]
    assert build_turn_handling(settings)["interruption"]["enabled"] is True
    assert build_silero_vad_kwargs()["min_silence_duration"] == 0.4
