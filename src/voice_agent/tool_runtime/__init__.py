"""Reusable, framework-agnostic runtime for agent tool calls.

Nothing in this package imports LiveKit. It is the piece intended to be lifted
into other projects unchanged; the LiveKit binding lives one layer up.
"""

from .errors import (
    PermanentToolError,
    ToolRuntimeError,
    ToolValidationError,
    TransientToolError,
)
from .idempotency import (
    IdempotencyLedger,
    InMemoryIdempotencyLedger,
    derive_key,
)
from .retry import RetryPolicy
from .runtime import (
    MAX_RAW_ARGS_BYTES,
    InvocationRecord,
    ToolOutcome,
    ToolRuntime,
    ToolSpec,
    ToolStatus,
)
from .sanitize import sanitize_args

__all__ = [
    "MAX_RAW_ARGS_BYTES",
    "IdempotencyLedger",
    "InMemoryIdempotencyLedger",
    "InvocationRecord",
    "PermanentToolError",
    "RetryPolicy",
    "ToolOutcome",
    "ToolRuntime",
    "ToolRuntimeError",
    "ToolSpec",
    "ToolStatus",
    "ToolValidationError",
    "TransientToolError",
    "derive_key",
    "sanitize_args",
]
