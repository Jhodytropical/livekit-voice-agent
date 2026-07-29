"""The framework's own tool-call log line must not carry caller PII.

Regression tests for run 3, defect 2. These import no LiveKit code: they assert
against the logger *name* LiveKit uses, so they still fail loudly if that name
changes on an upgrade rather than silently passing while PII escapes.
"""

from __future__ import annotations

import json
import logging

import pytest

from voice_agent.log_redaction import (
    LIVEKIT_LOGGER_NAME,
    UNPARSEABLE_PLACEHOLDER,
    ToolArgumentRedactor,
    install_tool_argument_redaction,
)

# The exact payload shape LiveKit Agents 1.6.7 attaches to "executing tool".
BOOKING_ARGS = json.dumps(
    {
        "slot_id": "2026-07-29T13:00",
        "caller_name": "Ada Lovelace",
        "phone": "3055550142",
        "email": "ada@example.com",
        "reason": "dental appointment",
    }
)


def _record(msg: str = "executing tool", **extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name=LIVEKIT_LOGGER_NAME,
        level=logging.DEBUG,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


@pytest.fixture
def redactor() -> ToolArgumentRedactor:
    return ToolArgumentRedactor()


def test_caller_pii_is_masked(redactor: ToolArgumentRedactor) -> None:
    record = _record(arguments=BOOKING_ARGS, function="book_appointment")

    assert redactor.filter(record) is True
    masked = json.loads(record.arguments)

    assert masked["caller_name"] == "A***[redacted]"
    assert masked["phone"] == "******0142"
    assert masked["email"] == "a***@[redacted]"
    assert masked["reason"] == "d***[redacted]"


def test_raw_values_are_absent_from_the_rendered_line(redactor: ToolArgumentRedactor) -> None:
    """The real assertion: grep the final string, not the parsed fields."""
    record = _record(arguments=BOOKING_ARGS, function="book_appointment")
    redactor.filter(record)

    for secret in ("Ada Lovelace", "3055550142", "ada@example.com"):
        assert secret not in record.arguments


def test_non_sensitive_fields_survive(redactor: ToolArgumentRedactor) -> None:
    """Redaction must not destroy the line's diagnostic value."""
    record = _record(arguments=BOOKING_ARGS, function="book_appointment")
    redactor.filter(record)

    assert json.loads(record.arguments)["slot_id"] == "2026-07-29T13:00"


def test_mock_tool_message_is_covered(redactor: ToolArgumentRedactor) -> None:
    record = _record(msg="executing mock tool", arguments=BOOKING_ARGS)
    redactor.filter(record)

    assert "Ada Lovelace" not in record.arguments


def test_unrelated_records_pass_through_untouched(redactor: ToolArgumentRedactor) -> None:
    record = _record(msg="received user transcript", user_transcript="my name is Ada Lovelace")

    assert redactor.filter(record) is True
    assert record.user_transcript == "my name is Ada Lovelace"


@pytest.mark.parametrize(
    "raw",
    ["not json at all", '["a", "list"]', '"a bare string"', "", "{unclosed", 12345],
)
def test_unparseable_arguments_fail_closed(redactor: ToolArgumentRedactor, raw: object) -> None:
    """Anything the parser cannot handle is dropped, never passed through."""
    record = _record(arguments=raw)
    redactor.filter(record)

    assert record.arguments == UNPARSEABLE_PLACEHOLDER


def test_record_without_arguments_is_left_alone(redactor: ToolArgumentRedactor) -> None:
    record = _record()

    assert redactor.filter(record) is True
    assert not hasattr(record, "arguments")


def test_install_is_idempotent() -> None:
    logger = logging.getLogger(LIVEKIT_LOGGER_NAME)
    before = list(logger.filters)
    try:
        first = install_tool_argument_redaction()
        second = install_tool_argument_redaction()

        assert first is second
        assert sum(isinstance(f, ToolArgumentRedactor) for f in logger.filters) == 1
    finally:
        logger.filters = before


def test_installed_filter_masks_through_the_real_logger(caplog: pytest.LogCaptureFixture) -> None:
    """End to end: emit the record LiveKit emits, read what a handler would see."""
    logger = logging.getLogger(LIVEKIT_LOGGER_NAME)
    before = list(logger.filters)
    try:
        install_tool_argument_redaction()
        with caplog.at_level(logging.DEBUG, logger=LIVEKIT_LOGGER_NAME):
            logger.debug(
                "executing tool",
                extra={"function": "book_appointment", "arguments": BOOKING_ARGS},
            )

        assert caplog.records, "the record never reached the handler"
        assert "Ada Lovelace" not in caplog.records[-1].arguments
    finally:
        logger.filters = before


# ---------------------------------------------------------------------------
# Transcript content — run 4 logged the caller's name four times through these
# lines, on a call whose tool arguments were fully masked.
# ---------------------------------------------------------------------------

from voice_agent.log_redaction import (  # noqa: E402
    TRANSCRIPT_ENV_VAR,
    TranscriptRedactor,
    install_transcript_redaction,
    transcripts_enabled,
)

SPOKEN = "my name is Ada Lovelace and my number is three oh five five five five"


@pytest.fixture
def transcripts() -> TranscriptRedactor:
    return TranscriptRedactor()


def test_spoken_content_is_replaced_by_its_word_count(transcripts: TranscriptRedactor) -> None:
    record = _record(
        msg="received user transcript",
        user_transcript=SPOKEN,
        language="en-US",
        transcript_delay=0.21,
    )

    assert transcripts.filter(record) is True
    assert record.user_transcript == f"[{len(SPOKEN.split())} words redacted]"
    assert "Ada Lovelace" not in record.user_transcript


def test_timing_fields_survive_redaction(transcripts: TranscriptRedactor) -> None:
    """Turn and latency analysis must still work on a redacted log."""
    record = _record(
        msg="received user transcript",
        user_transcript=SPOKEN,
        language="en-US",
        transcript_delay=0.21,
    )
    transcripts.filter(record)

    assert record.transcript_delay == 0.21
    assert record.language == "en-US"


def test_assistant_text_is_redacted_too(transcripts: TranscriptRedactor) -> None:
    """The agent echoes caller PII when it reads a number back."""
    record = _record(
        msg="conversation_item_added",
        role="assistant",
        text="To confirm, your number is 305-555-0142?",
    )
    transcripts.filter(record)

    assert "305-555-0142" not in record.text
    assert record.role == "assistant"


def test_singular_word_count_reads_correctly(transcripts: TranscriptRedactor) -> None:
    record = _record(msg="conversation_item_added", text="Goodbye")
    transcripts.filter(record)

    assert record.text == "[1 word redacted]"


def test_unrelated_records_are_untouched(transcripts: TranscriptRedactor) -> None:
    record = _record(msg="executing tool", arguments=BOOKING_ARGS)

    assert transcripts.filter(record) is True
    assert record.arguments == BOOKING_ARGS


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1", True),
        ("true", True),
        ("YES", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("", False),
        ("maybe", False),
    ],
)
def test_opt_in_parsing(value: str, expected: bool) -> None:
    assert transcripts_enabled({TRANSCRIPT_ENV_VAR: value}) is expected


def test_redaction_is_the_default_and_opt_in_disables_it() -> None:
    logger = logging.getLogger(LIVEKIT_LOGGER_NAME)
    before = list(logger.filters)
    try:
        assert install_transcript_redaction(redact=False) is None
        assert not any(isinstance(f, TranscriptRedactor) for f in logger.filters)

        installed = install_transcript_redaction(redact=True)
        assert isinstance(installed, TranscriptRedactor)
        assert install_transcript_redaction(redact=True) is installed
    finally:
        logger.filters = before
