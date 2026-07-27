"""Argument sanitization for logs.

Tool arguments on an appointment-booking call are, by definition, caller PII:
name, phone, email. Those belong in the CRM, not in a log line that gets shipped
to a third-party aggregator. Sanitizing here rather than at the log sink means a
new log destination cannot accidentally start receiving raw PII.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

MAX_LOGGED_VALUE_CHARS = 200

# Substring match, lowercased. Deliberately broad: a field named
# `customer_email_address` should be caught without anyone remembering to add it.
_SENSITIVE_HINTS = (
    "email",
    "phone",
    "mobile",
    "name",
    "address",
    "note",
    "reason",
    "password",
    "secret",
    "token",
    "key",
    "ssn",
    "dob",
)


def _mask_email(value: str) -> str:
    local, _, domain = value.partition("@")
    if not domain:
        return "[redacted]"
    return f"{local[:1]}***@[redacted]"


def _mask_phone(value: str) -> str:
    digits = [c for c in value if c.isdigit()]
    if len(digits) < 4:
        return "[redacted]"
    return "*" * (len(digits) - 4) + "".join(digits[-4:])


def _mask_generic(value: str) -> str:
    if not value:
        return "[redacted]"
    return f"{value[:1]}***[redacted]"


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(hint in lowered for hint in _SENSITIVE_HINTS)


def _sanitize_value(key: str, value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _sanitize_value(k, v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(key, v) for v in value]
    if not isinstance(value, str):
        # Numbers and booleans are not PII in this schema, but an unbounded
        # object still should not reach the log verbatim.
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return _truncate(str(value))

    if _is_sensitive(key):
        lowered = key.lower()
        if "email" in lowered:
            return _mask_email(value)
        if "phone" in lowered or "mobile" in lowered:
            return _mask_phone(value)
        return _mask_generic(value)
    return _truncate(value)


def _truncate(value: str) -> str:
    if len(value) <= MAX_LOGGED_VALUE_CHARS:
        return value
    return value[:MAX_LOGGED_VALUE_CHARS] + f"...[+{len(value) - MAX_LOGGED_VALUE_CHARS} chars]"


def sanitize_args(args: Mapping[str, Any]) -> dict[str, Any]:
    """Return a log-safe copy of ``args``.

    Sensitive fields are masked, everything else is truncated. The shape is
    preserved so a log line still tells you which arguments were supplied.
    """
    return {key: _sanitize_value(key, value) for key, value in args.items()}
