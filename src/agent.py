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

import logging

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import AgentServer, AgentSession, JobProcess
from livekit.plugins import deepgram, openai, silero

from voice_agent.appointment_agent import build_agent
from voice_agent.config import Settings
from voice_agent.instrumentation import instrument_barge_in
from voice_agent.log_redaction import (
    install_tool_argument_redaction,
    install_transcript_redaction,
    transcripts_enabled,
)
from voice_agent.turn_handling import build_silero_vad_kwargs, build_turn_handling

# .env.local matches the LiveKit CLI convention; .env is the fallback. Neither is
# committed — see .gitignore.
load_dotenv(".env.local")
load_dotenv()

logger = logging.getLogger("voice_agent")

# At import, not inside an entrypoint: every LiveKit job runs in its own process
# that re-imports this module, and the framework can log a tool call before any
# per-session setup would have run. Run 3 (2026-07-28) found raw caller PII in the
# framework's own DEBUG line, two milliseconds ahead of the runtime's sanitized one.
# See docs/acceptance-findings.md, run 3, defect 2.
install_tool_argument_redaction()

# Transcript content is dropped by default and kept only when LOG_TRANSCRIPTS is set
# deliberately, for an acceptance run. Word counts and every timing field survive
# either way. See log_redaction.py for why free-form speech gets a different
# treatment from structured arguments.
install_transcript_redaction()

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
    # Stated explicitly so a log can never be mistaken for the other mode.
    logger.info(
        "transcript logging: %s",
        "ENABLED — this log will contain caller speech" if transcripts_enabled() else "redacted",
    )

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

    instrument_barge_in(session)

    await session.start(room=ctx.room, agent=build_agent(settings))
    await session.generate_reply(instructions=GREETING)


if __name__ == "__main__":
    agents.cli.run_app(server)
