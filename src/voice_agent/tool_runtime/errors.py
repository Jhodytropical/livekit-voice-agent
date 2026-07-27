"""Error taxonomy for the tool runtime.

The taxonomy exists for one reason: the runtime must know what is safe to retry.
Anything a handler raises that is not one of these is treated as an unexpected
bug — logged in full, but reported to the caller as a generic failure and never
retried, because a bug is not going to fix itself on attempt two.
"""

from __future__ import annotations


class ToolRuntimeError(Exception):
    """Base class for every error the tool runtime understands."""


class TransientToolError(ToolRuntimeError):
    """A downstream fault that a retry could plausibly clear.

    Raise for timeouts, 429s, 5xx, connection resets. Never raise for a
    business-rule rejection — a slot that is already taken will still be taken
    on attempt two.
    """


class PermanentToolError(ToolRuntimeError):
    """A fault that retrying cannot fix: bad reference, business-rule rejection.

    Raise this for "slot already booked", "unknown customer", "outside business
    hours". The runtime reports it to the caller without burning retries.
    """


class ToolValidationError(ToolRuntimeError):
    """Arguments failed schema validation. Never retried, never executed."""
