"""Entrypoint: inbound appointment-booking voice agent on LiveKit Agents 1.6.7.

Run it:
    .venv/bin/python src/agent.py console   # terminal, local mic/speakers
    .venv/bin/python src/agent.py dev       # connect to LiveKit, use the browser console
    .venv/bin/python src/agent.py start     # production mode

Structure follows the official Python quickstart for this version — an
``AgentServer`` with an ``@server.rtc_session`` entrypoint, and
``agents.cli.run_app(server)`` at the bottom.

Sources:
  https://docs.livekit.io/agents/start/voice-ai.md          (AgentServer, rtc_session, run_app)
  https://docs.livekit.io/reference/agents/turn-handling-options.md
  https://docs.livekit.io/agents/logic/turns/vad.md         (Silero VAD)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import AgentServer, AgentSession, JobProcess
from livekit.plugins import deepgram, openai, silero

from voice_agent.appointment_agent import build_agent
from voice_agent.config import Settings
from voice_agent.turn_handling import build_silero_vad_kwargs, build_turn_handling

# .env.local matches the LiveKit CLI convention; .env is the fallback. Neither is
# committed — see .gitignore.
load_dotenv(".env.local")
load_dotenv()

logger = logging.getLogger("voice_agent")

AGENT_NAME = "appointment-agent"

GREETING = (
    "Greet the caller in one short sentence, say you can check the schedule and "
    "book an appointment, and invite them to interrupt you at any time."
)


def prewarm(proc: JobProcess) -> None:
    """Load the Silero VAD model once per worker process, not once per call.

    Source: https://docs.livekit.io/agents/logic/turns/vad.md
    """
    proc.userdata["vad"] = silero.VAD.load(**build_silero_vad_kwargs())


server = AgentServer(setup_fnc=prewarm)


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: agents.JobContext) -> None:
    settings = Settings.from_env()
    logger.info("starting session: %s", settings.describe())

    # AgentSession is generic over the session userdata type. This agent keeps no
    # per-session state of its own — the tool runtime owns everything stateful —
    # so the parameter is None.
    session: AgentSession[None] = AgentSession(
        stt=deepgram.STT(model=settings.stt_model, language=settings.stt_language),
        llm=openai.LLM(model=settings.llm_model),
        tts=openai.TTS(model=settings.tts_model, voice=settings.tts_voice),
        vad=ctx.proc.userdata.get("vad") or silero.VAD.load(**build_silero_vad_kwargs()),
        turn_handling=build_turn_handling(settings),
        # Bound the tool loop. Without a cap, one bad model turn can chain tool
        # calls until the caller hangs up. Three is enough for
        # check_availability -> book_appointment -> confirm.
        max_tool_steps=3,
    )

    _instrument_barge_in(session)

    await session.start(room=ctx.room, agent=build_agent(settings))
    await session.generate_reply(instructions=GREETING)


def _instrument_barge_in(session: AgentSession[None]) -> None:
    """Log when the agent starts and stops speaking, and when the user starts.

    Why this exists: on 2026-07-27 we tried to measure barge-in latency from the
    default logs and could not. The only visible signal was that an assistant
    ``conversation_item_added`` line carried truncated text — but that line is
    emitted as bookkeeping when the *user's* turn commits, so it lands 2-5 ms
    after the final STT transcript every single time, regardless of when audio
    actually stopped. Three different interruption configs produced the same
    2-5 ms offset, which is the giveaway: we were measuring the logger, not the
    agent.

    The pair below is the actual measurement. ``user_state`` flips to "speaking"
    on VAD onset; ``agent_state`` leaves "speaking" when playout stops. The
    difference between those two timestamps is barge-in latency, and nothing
    else in the pipeline sits between them.

    Source: https://docs.livekit.io/agents/build/events/
    """
    import time

    marks: dict[str, float] = {}

    @session.on("user_state_changed")
    def _on_user(ev: Any) -> None:
        if getattr(ev, "new_state", None) == "speaking":
            marks["user_speaking_at"] = time.time()
            logger.info(
                json.dumps(
                    {
                        "event": "user_started_speaking",
                        "timestamp": round(marks["user_speaking_at"], 4),
                    }
                )
            )

    @session.on("agent_state_changed")
    def _on_agent(ev: Any) -> None:
        old_state = getattr(ev, "old_state", None)
        new_state = getattr(ev, "new_state", None)
        if new_state == "speaking":
            marks["agent_speaking_at"] = time.time()
            logger.info(
                json.dumps(
                    {
                        "event": "agent_started_speaking",
                        "timestamp": round(marks["agent_speaking_at"], 4),
                    }
                )
            )
        elif old_state == "speaking":
            now = time.time()
            started = marks.get("user_speaking_at")
            # Only a stop that follows a user onset is a barge-in. A stop with no
            # preceding onset is the agent simply finishing its sentence.
            overlap = (
                now - started if started and started > marks.get("agent_speaking_at", 0) else None
            )
            logger.info(
                json.dumps(
                    {
                        "event": "agent_stopped_speaking",
                        "timestamp": round(now, 4),
                        "to_state": new_state,
                        "barge_in_latency_ms": round(overlap * 1000, 1) if overlap else None,
                    }
                )
            )


if __name__ == "__main__":
    agents.cli.run_app(server)
