"""The tool runtime: one entry point that every tool call goes through.

Design intent (this is the piece meant to lift into client work unchanged):

* ``register`` is the only way a tool becomes callable, and it demands a Pydantic
  model. There is no path that executes a handler on unvalidated arguments.
* ``invoke`` derives the idempotency key itself. A caller cannot pass a wrong key
  or forget one.
* Validation, keying, retry, logging and error mapping all happen in one place,
  so a new tool inherits every guarantee by existing.
* Nothing here imports LiveKit. The LiveKit ``@function_tool`` wrappers are a thin
  adapter layer on top (see ``voice_agent.appointment_agent``), which keeps the
  runtime testable without a room, a model, or a credential.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ValidationError

from .errors import PermanentToolError, ToolValidationError, TransientToolError
from .idempotency import IdempotencyLedger, InMemoryIdempotencyLedger, derive_key
from .retry import RetryPolicy
from .sanitize import sanitize_args

logger = logging.getLogger("voice_agent.tool_runtime")

ArgsT = TypeVar("ArgsT", bound=BaseModel)
ResultT = TypeVar("ResultT")

# A tool-call payload from an LLM has no business being large. Capping it bounds
# both memory and the blast radius of a prompt-injection attempt that tries to
# stuff a payload through a tool argument.
MAX_RAW_ARGS_BYTES = 4096

ToolStatus = Literal["ok", "replayed", "invalid", "failed"]


@dataclass(frozen=True)
class ToolSpec(Generic[ArgsT, ResultT]):
    """Everything the runtime needs to call one tool safely.

    Args:
        name: Stable tool name. Also the first component of the idempotency key.
        args_model: Pydantic model validating the LLM-supplied arguments. Use
            ``extra="forbid"`` so invented arguments are rejected rather than
            silently dropped.
        handler: The actual work. Raise ``TransientToolError`` for retryable
            faults and ``PermanentToolError`` for business-rule rejections.
        once_per_turn: ``True`` for anything that mutates state — the ledger then
            guarantees at-most-once execution per (tool, turn, args). ``False``
            for read-only lookups, which should return fresh data every time.
    """

    name: str
    args_model: type[ArgsT]
    handler: Callable[[ArgsT], Awaitable[ResultT]]
    once_per_turn: bool = False


@dataclass(frozen=True)
class ToolOutcome(Generic[ResultT]):
    """The single result shape every tool call returns. No exceptions escape
    ``invoke`` for expected failure modes, so a caller cannot forget a try block.
    """

    status: ToolStatus
    tool: str
    turn_id: str
    idempotency_key: str
    result: ResultT | None = None
    error: str | None = None
    attempts: int = 0
    latency_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status in ("ok", "replayed")


@dataclass(frozen=True)
class InvocationRecord:
    """One structured log line per tool invocation."""

    turn_id: str
    tool: str
    idempotency_key: str
    args: Mapping[str, Any]
    status: ToolStatus
    attempts: int
    latency_ms: float
    error: str | None = None
    timestamp: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "event": "tool_invocation",
            "timestamp": self.timestamp,
            "turn_id": self.turn_id,
            "tool": self.tool,
            "idempotency_key": self.idempotency_key,
            "args": dict(self.args),
            "status": self.status,
            "attempts": self.attempts,
            "latency_ms": round(self.latency_ms, 2),
            "error": self.error,
        }


def _default_record_sink(record: InvocationRecord) -> None:
    logger.info(json.dumps(record.as_dict(), default=str))


class ToolRuntime:
    """Validated, idempotent, bounded-retry execution of agent tools."""

    def __init__(
        self,
        *,
        ledger: IdempotencyLedger | None = None,
        retry: RetryPolicy | None = None,
        on_record: Callable[[InvocationRecord], None] = _default_record_sink,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._ledger = ledger if ledger is not None else InMemoryIdempotencyLedger()
        self._retry = retry if retry is not None else RetryPolicy()
        self._on_record = on_record
        self._sleep = sleep
        self._clock = clock
        self._specs: dict[str, ToolSpec[Any, Any]] = {}

    # ---- registration -------------------------------------------------

    def register(self, spec: ToolSpec[Any, Any]) -> None:
        if spec.name in self._specs:
            raise ValueError(f"tool {spec.name!r} is already registered")
        self._specs[spec.name] = spec

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._specs))

    # ---- invocation ---------------------------------------------------

    async def invoke(
        self, name: str, *, turn_id: str, raw_args: Mapping[str, Any]
    ) -> ToolOutcome[Any]:
        """Validate, key, execute (at most once per turn for writes), log.

        Raises ``KeyError`` only when ``name`` was never registered — that is a
        programming error, not a runtime condition, so it is loud.
        """
        spec = self._specs[name]
        started = self._clock()

        try:
            args = self._validate(spec, raw_args)
        except ToolValidationError as exc:
            key = derive_key(tool=name, turn_id=turn_id, args={"__invalid__": True})
            return self._finish(
                ToolOutcome(
                    status="invalid",
                    tool=name,
                    turn_id=turn_id,
                    idempotency_key=key,
                    error=str(exc),
                    attempts=0,
                    latency_ms=(self._clock() - started) * 1000,
                ),
                logged_args=sanitize_args(_shallow_str_map(raw_args)),
            )

        validated = args.model_dump(mode="json")
        key = derive_key(tool=name, turn_id=turn_id, args=validated)
        logged_args = sanitize_args(validated)
        attempts_holder: list[int] = [0]

        async def execute() -> Any:
            return await self._run_with_retry(spec, args, attempts_holder)

        try:
            if spec.once_per_turn:
                result, executed = await self._ledger.run_once(key, execute)
            else:
                result, executed = await execute(), True
        except (TransientToolError, PermanentToolError) as exc:
            return self._finish(
                ToolOutcome(
                    status="failed",
                    tool=name,
                    turn_id=turn_id,
                    idempotency_key=key,
                    error=str(exc),
                    attempts=attempts_holder[0],
                    latency_ms=(self._clock() - started) * 1000,
                ),
                logged_args=logged_args,
            )
        except Exception:
            # Unexpected exception: log the detail locally, return a generic
            # message. Handler internals (DSNs, credentials, stack context) must
            # not reach the LLM or the transcript.
            logger.exception("tool %s raised an unexpected error", name)
            return self._finish(
                ToolOutcome(
                    status="failed",
                    tool=name,
                    turn_id=turn_id,
                    idempotency_key=key,
                    error="internal error",
                    attempts=attempts_holder[0],
                    latency_ms=(self._clock() - started) * 1000,
                ),
                logged_args=logged_args,
            )

        return self._finish(
            ToolOutcome(
                status="ok" if executed else "replayed",
                tool=name,
                turn_id=turn_id,
                idempotency_key=key,
                result=result,
                attempts=attempts_holder[0],
                latency_ms=(self._clock() - started) * 1000,
            ),
            logged_args=logged_args,
        )

    # ---- internals ----------------------------------------------------

    def _validate(self, spec: ToolSpec[ArgsT, Any], raw_args: Mapping[str, Any]) -> ArgsT:
        try:
            encoded = json.dumps(dict(raw_args), default=str)
        except (TypeError, ValueError) as exc:
            raise ToolValidationError("arguments are not JSON-serializable") from exc
        if len(encoded.encode("utf-8")) > MAX_RAW_ARGS_BYTES:
            raise ToolValidationError(f"arguments too large (limit {MAX_RAW_ARGS_BYTES} bytes)")
        try:
            return spec.args_model.model_validate(dict(raw_args))
        except ValidationError as exc:
            raise ToolValidationError(_summarize_validation_error(exc)) from exc

    async def _run_with_retry(
        self, spec: ToolSpec[ArgsT, Any], args: ArgsT, attempts_holder: list[int]
    ) -> Any:
        last: Exception | None = None
        for attempt in range(1, self._retry.max_attempts + 1):
            attempts_holder[0] = attempt
            try:
                return await asyncio.wait_for(spec.handler(args), timeout=self._retry.timeout)
            except (TransientToolError, asyncio.TimeoutError) as exc:
                last = (
                    exc
                    if isinstance(exc, TransientToolError)
                    else TransientToolError(f"{spec.name} timed out")
                )
                if attempt >= self._retry.max_attempts:
                    break
                await self._sleep(self._retry.delay_for(attempt))
        assert last is not None  # noqa: S101 - loop always sets it before breaking
        raise last

    def _finish(
        self, outcome: ToolOutcome[Any], *, logged_args: Mapping[str, Any]
    ) -> ToolOutcome[Any]:
        record = InvocationRecord(
            turn_id=outcome.turn_id,
            tool=outcome.tool,
            idempotency_key=outcome.idempotency_key,
            args=logged_args,
            status=outcome.status,
            attempts=outcome.attempts,
            latency_ms=outcome.latency_ms,
            error=outcome.error,
        )
        try:
            self._on_record(record)
        except Exception:  # a broken log sink must never break a live call
            logger.exception("invocation record sink failed")
        return outcome


def _shallow_str_map(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {str(k): v for k, v in raw.items()}


def _summarize_validation_error(exc: ValidationError) -> str:
    """Field names and reasons only.

    Pydantic's default message embeds the offending input, which then travels
    back to the LLM and into the log. On a call carrying PII — or a payload
    someone slipped in via prompt injection — that is exactly the thing not to
    echo.
    """
    parts = []
    for err in exc.errors():
        location = ".".join(str(p) for p in err.get("loc", ())) or "<root>"
        parts.append(f"{location}: {err.get('type', 'invalid')}")
    return "invalid arguments — " + "; ".join(parts[:6])
