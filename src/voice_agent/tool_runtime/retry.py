"""Bounded retry policy.

Every knob has a hard ceiling. An agent that can retry without limit is an agent
that can spend money without limit, and on a voice call it also means dead air.
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_ALLOWED_ATTEMPTS = 5
MAX_ALLOWED_DELAY_SECONDS = 10.0
MAX_ALLOWED_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff, capped in both delay and attempt count.

    Backoff is deterministic by default (``jitter=0.0``) so tests can assert the
    exact schedule. Set ``jitter`` above zero in production to avoid synchronised
    retry storms across concurrent calls.
    """

    max_attempts: int = 3
    base_delay: float = 0.2
    max_delay: float = 2.0
    timeout: float = 5.0
    jitter: float = 0.0

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= MAX_ALLOWED_ATTEMPTS:
            raise ValueError(f"max_attempts must be between 1 and {MAX_ALLOWED_ATTEMPTS}")
        if not 0 < self.base_delay <= MAX_ALLOWED_DELAY_SECONDS:
            raise ValueError(f"base_delay must be in (0, {MAX_ALLOWED_DELAY_SECONDS}]")
        if not self.base_delay <= self.max_delay <= MAX_ALLOWED_DELAY_SECONDS:
            raise ValueError("max_delay must be >= base_delay and within the allowed ceiling")
        if not 0 < self.timeout <= MAX_ALLOWED_TIMEOUT_SECONDS:
            raise ValueError(f"timeout must be in (0, {MAX_ALLOWED_TIMEOUT_SECONDS}]")
        if not 0.0 <= self.jitter <= 1.0:
            raise ValueError("jitter must be in [0, 1]")

    def delay_for(self, attempt: int) -> float:
        """Delay in seconds to wait *after* a failed ``attempt`` (1-based)."""
        if attempt < 1:
            raise ValueError("attempt is 1-based")
        raw = self.base_delay * float(2 ** (attempt - 1))
        return min(raw, self.max_delay)
