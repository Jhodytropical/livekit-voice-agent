"""Redact caller PII from LiveKit's own tool-call debug line.

Sanitizing inside ``ToolRuntime`` is not enough, and run 3 proved it. On
2026-07-28 the runtime logged exactly what it promised::

    "args": {"caller_name": "J***[redacted]", "phone": "******5555", ...}

…and two milliseconds earlier LiveKit's own logger had already written the
unredacted arguments to the same file::

    DEBUG livekit.agents - executing tool {"function": "book_appointment",
      "arguments": "{\\"caller_name\\": \\"<real name>\\", \\"phone\\": \\"<10 digits>\\", ...}"}

``sanitize.py`` claims that sanitizing at the runtime rather than the sink means
"a new log destination cannot accidentally start receiving raw PII." That claim
was true of this project's log lines and false of the log file as a whole,
because this project is not the only writer to it.

The fix keeps the claim honest by putting the *same* sanitizer in front of the
other writer. A ``logging.Filter`` on the ``livekit.agents`` logger rewrites the
``arguments`` field in place before any handler sees the record. One definition
of "sensitive" now governs both writers.

Why a filter and not ``setLevel(logging.INFO)``: the framework's DEBUG stream is
where ``received user transcript``, ``user turn committed`` and ``aec warmup
active`` live, and those lines are what made runs 1-3 diagnosable at all.
Silencing the whole stream to hide one field would cost more than it saves — and
it would leave the guarantee depending on which CLI verb someone typed.

See ``docs/acceptance-findings.md``, run 3, defect 2.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from .tool_runtime import sanitize_args

#: LiveKit Agents 1.6.7 logs every tool call through one logger, defined in
#: ``livekit/agents/log.py`` as ``logging.getLogger("livekit.agents")``. A filter
#: attached to a logger only sees records emitted through that logger, so this
#: name has to match exactly; it is asserted in the tests.
LIVEKIT_LOGGER_NAME = "livekit.agents"

#: ``livekit/agents/voice/generation.py`` emits one of these two messages with the
#: raw arguments attached as ``extra={"arguments": ...}``.
_TOOL_CALL_MESSAGES = frozenset({"executing tool", "executing mock tool"})

#: Used when the arguments cannot be parsed. See ``_redact`` for why this is not
#: a pass-through.
UNPARSEABLE_PLACEHOLDER = "[redacted: tool arguments could not be parsed]"


def _redact(raw: Any) -> str:
    """Return a log-safe rendering of a raw tool-argument payload.

    **Fails closed.** Anything this function cannot parse into a mapping is
    replaced wholesale rather than passed through. A redactor that emits the
    original value whenever it is confused is not a redactor — the one payload
    shaped unusually enough to break the parser is exactly the one worth not
    printing.
    """
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return UNPARSEABLE_PLACEHOLDER
    else:
        parsed = raw

    if not isinstance(parsed, dict):
        return UNPARSEABLE_PLACEHOLDER

    try:
        return json.dumps(sanitize_args(parsed))
    except (TypeError, ValueError):
        return UNPARSEABLE_PLACEHOLDER


class ToolArgumentRedactor(logging.Filter):
    """Rewrites ``record.arguments`` on LiveKit's tool-call records.

    Always returns ``True``: this filter censors, it does not drop. Losing the
    line entirely would hide which tool ran, which is the diagnostically useful
    half of it.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.msg not in _TOOL_CALL_MESSAGES:
            return True
        raw = getattr(record, "arguments", None)
        if raw is None:
            return True
        record.arguments = _redact(raw)
        return True


def install_tool_argument_redaction(logger_name: str = LIVEKIT_LOGGER_NAME) -> ToolArgumentRedactor:
    """Attach the redactor to LiveKit's logger. Idempotent.

    Idempotence matters because every LiveKit job runs in its own process that
    re-imports the entrypoint module; installing twice would double-sanitize
    already-masked values into noise.
    """
    logger = logging.getLogger(logger_name)
    for existing in logger.filters:
        if isinstance(existing, ToolArgumentRedactor):
            return existing
    redactor = ToolArgumentRedactor()
    logger.addFilter(redactor)
    return redactor


# ---------------------------------------------------------------------------
# Transcript content
# ---------------------------------------------------------------------------
#
# Masking tool arguments closes the *structured* leak. It does nothing about the
# caller's own words, which reach the log verbatim through the framework's
# transcript lines: run 4 logged "John Doe" four times that way, from a call whose
# tool arguments were fully masked.
#
# Free-form speech cannot be masked field-by-field the way `caller_name` can —
# nothing marks which spoken words are sensitive. So the default here is to drop
# the content and keep the shape: word count and every timing field survive, which
# is what turn and latency analysis actually needs. Barge-in detection no longer
# depends on reading assistant text at all (see `instrumentation.py`), so nothing
# downstream breaks.
#
# Acceptance runs that need the words set LOG_TRANSCRIPTS=1 deliberately. Those
# logs contain caller speech and must not leave the machine — `logs/` is
# gitignored for exactly this reason.

TRANSCRIPT_ENV_VAR = "LOG_TRANSCRIPTS"

_TRANSCRIPT_FIELDS = {
    "received user transcript": ("user_transcript",),
    "conversation_item_added": ("text",),
}


def _summarize(value: Any) -> str:
    """Replace speech with its shape: word count only."""
    if not isinstance(value, str):
        return "[redacted]"
    words = len(value.split())
    return f"[{words} word{'' if words == 1 else 's'} redacted]"


class TranscriptRedactor(logging.Filter):
    """Strips spoken content from LiveKit's transcript records, keeping word counts."""

    def filter(self, record: logging.LogRecord) -> bool:
        fields = _TRANSCRIPT_FIELDS.get(record.msg)
        if not fields:
            return True
        for field in fields:
            value = getattr(record, field, None)
            if value is not None:
                setattr(record, field, _summarize(value))
        return True


def transcripts_enabled(env: Any = None) -> bool:
    """True when LOG_TRANSCRIPTS is set to an affirmative value. Default False."""
    raw = (env or os.environ).get(TRANSCRIPT_ENV_VAR, "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def install_transcript_redaction(
    logger_name: str = LIVEKIT_LOGGER_NAME, *, redact: bool | None = None
) -> TranscriptRedactor | None:
    """Attach the transcript redactor. Idempotent.

    Args:
        logger_name: Logger to filter.
        redact: ``None`` decides from ``LOG_TRANSCRIPTS`` — redact unless it is set.
            ``True`` or ``False`` overrides that, which is what tests use.

    Returns:
        The installed filter, or ``None`` when redaction was deliberately skipped,
        so a caller can report which mode is active instead of guessing.
    """
    should_redact = (not transcripts_enabled()) if redact is None else redact
    if not should_redact:
        return None
    logger = logging.getLogger(logger_name)
    for existing in logger.filters:
        if isinstance(existing, TranscriptRedactor):
            return existing
    redactor = TranscriptRedactor()
    logger.addFilter(redactor)
    return redactor
