"""Offline stand-ins for the audio path.

These let a real ``AgentSession`` run its real speech scheduling and playout code
with no network, no model and no credential. They fake the *edges* of the
pipeline (a TTS that emits silence, a speaker that consumes in real time) and
nothing in between, so the code under test is LiveKit's, not ours.
"""

from __future__ import annotations

import asyncio

from livekit.agents import tts as lk_tts
from livekit.agents.types import APIConnectOptions
from livekit.agents.voice import io as vio

SAMPLE_RATE = 24_000
NUM_CHANNELS = 1
CHUNK_MS = 100

_DEFAULT_CONN_OPTIONS = APIConnectOptions()


class _SilenceStream(lk_tts.ChunkedStream):
    def __init__(
        self, *, tts: lk_tts.TTS, input_text: str, conn_options: APIConnectOptions, seconds: float
    ) -> None:
        self._seconds = seconds
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)

    async def _run(self, output_emitter: lk_tts.AudioEmitter) -> None:
        output_emitter.initialize(
            request_id="fake-tts",
            sample_rate=SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
            mime_type="audio/pcm",
            stream=False,
        )
        chunk = b"\x00\x00" * int(SAMPLE_RATE * CHUNK_MS / 1000)
        for _ in range(int(self._seconds * 1000 / CHUNK_MS)):
            output_emitter.push(chunk)
        output_emitter.flush()


class SilenceTTS(lk_tts.TTS):
    """Emits a fixed duration of silent PCM instead of calling a TTS provider."""

    def __init__(self, *, seconds: float = 4.0) -> None:
        super().__init__(
            capabilities=lk_tts.TTSCapabilities(streaming=False),
            sample_rate=SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
        )
        self._seconds = seconds

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = _DEFAULT_CONN_OPTIONS
    ) -> lk_tts.ChunkedStream:
        return _SilenceStream(
            tts=self, input_text=text, conn_options=conn_options, seconds=self._seconds
        )


class PacedAudioOutput(vio.AudioOutput):
    """A speaker that buffers audio and plays it back at real time.

    Two properties matter, and both are required for an interruption assertion to
    mean anything:

    * **Buffered.** ``capture_frame`` returns immediately — the base class
      contract says frames may be pushed faster than real time — and a background
      task drains the buffer at wall-clock speed. A sink that played
      synchronously inside ``capture_frame`` would misreport completion.
    * **Correct completion semantics.** ``flush()`` means "no more frames for
      this segment", not "playback finished". On an interrupt LiveKit calls
      ``flush()`` *then* ``clear_buffer()``, so a sink that reported completion
      from ``flush()`` would announce a clean finish for an utterance that was
      actually cut off.
    """

    def __init__(self) -> None:
        super().__init__(
            label="paced-test-output",
            capabilities=vio.AudioOutputCapabilities(pause=False),
            sample_rate=SAMPLE_RATE,
        )
        self._queue: asyncio.Queue[object] = asyncio.Queue()
        self._player: asyncio.Task[None] | None = None
        self._position = 0.0
        self.played_seconds = 0.0
        self.interrupted_segments = 0

    _END_OF_SEGMENT = object()

    async def capture_frame(self, frame) -> None:  # type: ignore[no-untyped-def]
        await super().capture_frame(frame)
        if self._player is None or self._player.done():
            self._position = 0.0
            self._player = asyncio.create_task(self._drain_at_real_time())
        self._queue.put_nowait(frame)

    def flush(self) -> None:
        super().flush()
        if self._player is not None and not self._player.done():
            self._queue.put_nowait(self._END_OF_SEGMENT)

    def clear_buffer(self) -> None:
        player = self._player
        if player is None or player.done():
            return
        player.cancel()
        self._empty_queue()
        self.interrupted_segments += 1
        self.on_playback_finished(playback_position=self._position, interrupted=True)

    async def _drain_at_real_time(self) -> None:
        while True:
            item = await self._queue.get()
            if item is self._END_OF_SEGMENT:
                self.on_playback_finished(playback_position=self._position, interrupted=False)
                return
            duration = item.duration  # type: ignore[attr-defined]
            await asyncio.sleep(duration)
            self._position += duration
            self.played_seconds += duration

    def _empty_queue(self) -> None:
        while not self._queue.empty():
            self._queue.get_nowait()
