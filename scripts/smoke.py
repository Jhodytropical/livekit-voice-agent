#!/usr/bin/env python
"""Credential-free startup and configuration smoke test.

Exercises everything the agent does before it touches the network: imports,
plugin resolution, settings parsing, VAD model load, turn-handling construction,
agent + tool registration, and one real tool round-trip through the runtime.

It never connects to LiveKit and never requires a credential. It loads .env.local
if present and reports which credentials are missing rather than failing on them, so it is useful both on a
laptop with no keys and in CI.

    .venv/bin/python scripts/smoke.py

Exit code 0 means the process would start; it does not mean a call would work.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import time
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

# Load .env.local (then .env) exactly as src/agent.py does. Without this the
# credential section below reports everything missing even when the file is
# correctly filled in — the smoke test would say BLOCKED while the agent runs fine.
try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env.local")
    load_dotenv(_ROOT / ".env")
except ImportError:  # python-dotenv is a runtime dep; tolerate its absence here
    pass

FAILURES: list[str] = []


def check(name: str, fn: Callable[[], Any]) -> Any:
    started = time.perf_counter()
    try:
        detail = fn()
    except Exception as exc:  # noqa: BLE001 - the whole point is to report, not raise
        FAILURES.append(name)
        print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
        return None
    elapsed = (time.perf_counter() - started) * 1000
    print(f"  ok    {name} ({elapsed:.0f} ms){'' if detail is None else f' — {detail}'}")
    return detail


def main() -> int:
    print("LiveKit voice agent — credential-free smoke test")
    print(f"  python {sys.version.split()[0]}")

    print("\n[1] imports and versions")

    def _versions() -> str:
        from importlib.metadata import version

        names = (
            "livekit-agents",
            "livekit-plugins-deepgram",
            "livekit-plugins-openai",
            "livekit-plugins-silero",
            "pydantic",
        )
        return ", ".join(f"{n}=={version(n)}" for n in names)

    check("installed package versions", _versions)
    check(
        "livekit.agents API surface",
        lambda: _require_attrs(
            "livekit.agents",
            ["AgentServer", "AgentSession", "Agent", "function_tool", "RunContext"],
        ),
    )
    check(
        "plugins import",
        lambda: _require_attrs("livekit.plugins.deepgram", ["STT"])
        and _require_attrs("livekit.plugins.openai", ["LLM", "TTS"])
        and _require_attrs("livekit.plugins.silero", ["VAD"])
        and "deepgram + openai + silero",
    )

    print("\n[2] configuration")
    from voice_agent.config import Settings

    settings = check("Settings.from_env()", Settings.from_env)
    if settings is None:
        return _finish()

    print("\n[3] turn detection and interruption")
    from voice_agent.turn_handling import build_silero_vad_kwargs, build_turn_handling

    turn_handling = check("build_turn_handling()", lambda: build_turn_handling(settings))
    check("build_silero_vad_kwargs()", build_silero_vad_kwargs)

    def _session_accepts_config() -> str:
        from livekit.agents import AgentSession

        AgentSession(turn_handling=turn_handling)
        return "AgentSession accepted turn_handling"

    check("AgentSession accepts turn_handling", _session_accepts_config)

    def _load_vad() -> str:
        from livekit.plugins import silero

        silero.VAD.load(**build_silero_vad_kwargs())
        return "Silero VAD model loaded locally"

    check("Silero VAD load", _load_vad)

    print("\n[4] agent and tools")
    from voice_agent.appointment_agent import build_agent

    agent = check("build_agent()", lambda: build_agent(settings))
    if agent is not None:
        check("tool registration", lambda: ", ".join(sorted(t.id for t in agent.tools)))

    print("\n[5] tool runtime round-trip (demo adapters, no external writes)")
    check("check_availability + book_appointment + duplicate guard", _tool_round_trip)

    print("\n[6] credentials")
    print(json.dumps(settings.describe(), indent=2, sort_keys=True))
    missing = settings.missing_credentials
    if missing:
        print(f"\n  NOTE: {len(missing)} credential(s) not set: {', '.join(missing)}")
        print("  The agent will start but cannot join a room or reach a model provider.")
        print("  A live browser call is BLOCKED until these are supplied.")
    else:
        print("\n  All credentials present.")

    _check_credential_shapes()

    return _finish()


# Presence is not correctness. On 2026-07-27 an Anthropic key (sk-ant-...) was pasted
# into OPENAI_API_KEY: smoke reported "all credentials present", the agent registered,
# joined the room, transcribed speech perfectly — and then died on every single turn
# with a 401 from OpenAI. Eight minutes of a recorded call were lost to a check that
# only asked "is the variable set?". These prefix rules are cheap and catch the whole
# class of paste-into-the-wrong-slot mistakes.
#
# Rules are deliberately loose: they reject keys that are provably from the wrong
# provider, not keys that merely look unfamiliar. A new prefix from a vendor must not
# turn into a false failure here.
_KEY_RULES = {
    # var: (must start with one of, must NOT start with any of, note)
    "OPENAI_API_KEY": (("sk-",), ("sk-ant-",), "an OpenAI key, not an Anthropic one"),
    "ANTHROPIC_API_KEY": (("sk-ant-",), (), "an Anthropic key"),
    "LIVEKIT_API_SECRET": ((), ("sk-",), "a LiveKit secret, not a model-provider key"),
}


def _check_credential_shapes() -> None:
    """Reject credentials that are provably from the wrong provider."""
    import os

    problems = []
    for var, (must, must_not, note) in _KEY_RULES.items():
        val = (os.environ.get(var) or "").strip()
        if not val:
            continue
        if any(val.startswith(bad) for bad in must_not) or (
            must and not any(val.startswith(good) for good in must)
        ):
            shown = val[:7] + "..." if len(val) > 7 else "(short)"
            problems.append(f"{var} starts with {shown} — expected {note}")

    if problems:
        for line in problems:
            print(f"  WRONG PROVIDER: {line}")
        FAILURES.append("credential shape")
    else:
        print("  Key prefixes match their providers — a live call is possible.")


def _require_attrs(module_name: str, attrs: list[str]) -> str:
    import importlib

    module = importlib.import_module(module_name)
    missing = [a for a in attrs if not hasattr(module, a)]
    if missing:
        raise AttributeError(f"{module_name} is missing {missing}")
    return ", ".join(attrs)


def _next_weekday(after: date) -> date:
    """The first Mon–Fri strictly after ``after``.

    The demo calendar only opens on weekdays and refuses dates in the past, so a
    hard-coded date would rot. Resolved once per run and threaded through every
    assertion below, which keeps a single run deterministic.
    """
    day = after + timedelta(days=1)
    while day.weekday() in (5, 6):
        day += timedelta(days=1)
    return day


def _tool_round_trip() -> str:
    from voice_agent.appointments import DemoCalendar, DemoCrm, build_appointment_runtime

    calendar = DemoCalendar()
    target_day = _next_weekday(calendar.today)
    runtime = build_appointment_runtime(calendar=calendar, crm=DemoCrm(), on_record=lambda r: None)

    # Explicit raises rather than `assert`: this script must behave identically
    # under `python -O`, which strips assertions.
    def expect(condition: bool, message: str) -> None:
        if not condition:
            raise RuntimeError(message)

    async def run() -> str:
        avail = await runtime.invoke(
            "check_availability", turn_id="smoke", raw_args={"date": target_day.isoformat()}
        )
        expect(avail.ok, f"check_availability failed: {avail.error}")
        slots = (avail.result or {}).get("slots") or []
        expect(bool(slots), f"check_availability returned no slots for {target_day} (a weekday)")
        slot = slots[0]

        args = {"slot_id": slot, "caller_name": "Smoke Test", "phone": "+13055550100"}
        first = await runtime.invoke("book_appointment", turn_id="smoke", raw_args=args)
        second = await runtime.invoke("book_appointment", turn_id="smoke", raw_args=args)
        expect(first.status == "ok", f"first booking failed: {first.error}")
        expect(second.status == "replayed", f"duplicate not replayed: {second.status}")
        expect(calendar.booking_count == 1, f"double booked: {calendar.booking_count}")

        bad = await runtime.invoke(
            "book_appointment",
            turn_id="smoke",
            raw_args={**args, "phone": "not a phone number"},
        )
        expect(bad.status == "invalid", f"bad phone was not rejected: {bad.status}")
        return f"{target_day}: {len(slots)} slots, 1 booking from 2 calls, bad input rejected"

    return asyncio.run(run())


def _finish() -> int:
    if FAILURES:
        print(f"\nSMOKE FAILED — {len(FAILURES)} check(s): {', '.join(FAILURES)}")
        return 1
    print("\nSMOKE PASSED — config valid, and any credentials present are the right shape.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
