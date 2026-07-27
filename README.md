# livekit-voice-agent

An inbound **appointment-booking / qualification** voice agent built on
[LiveKit Agents](https://docs.livekit.io/agents.md) for Python, plus a reusable
tool runtime that makes agent tool calls validated, idempotent and bounded.

Two things are worth taking from this repo:

1. **`src/voice_agent/tool_runtime/`** — a framework-agnostic runtime that no
   voice agent touching a calendar or a CRM should be without. Pydantic
   validation, per-call-turn idempotency, bounded retry with backoff, and one
   structured log line per invocation with PII masked. It imports nothing from
   LiveKit and lifts into other projects unchanged.
2. **Explicit barge-in configuration**, unit-tested rather than tuned by ear.

Built against **`livekit-agents==1.6.7`** (released 2026-07-25) on Python 3.12.8.

---

## Status, honestly

| | |
|---|---|
| Tool runtime | Built, 67 automated tests passing |
| Live browser call | **Run — 2026-07-27.** Four sessions against LiveKit Cloud, real audio both directions |
| Refusal to invent availability | **Verified live**, under pressure. See [Live findings](#live-findings-2026-07-27) |
| Date handling | **Bug found live and fixed.** See [Live findings](#live-findings-2026-07-27) |
| Barge-in — does it interrupt? | **Verified live.** Speech truncates mid-phrase, caller's words survive intact, agent answers the interruption |
| Barge-in — *how fast*, in ms | **Not measured.** Instrumentation added after the first attempt failed; see [Blocked](#blocked--not-verified) |
| Recorded demo call | Screen recording captured; not yet cut |

Nothing in this README claims a working cloud session it didn't have. Every
"verified" statement maps either to a command in [Verification](#verification) or
to a timestamped line in a call log quoted in [Live findings](#live-findings-2026-07-27).

---

## Live findings (2026-07-27)

First live audio. Four sessions, ~20 conversational turns. Two findings changed
the code; one measurement attempt failed and is reported as failed.

### 1. The agent had no idea what day it was — fixed

Before the fix, with no date in the system prompt:

```
user: "Need an appointment for dental."
tool: check_availability {"date": "2024-06-03"} -> failed: "that date is in the past"
user: "Yeah. Tomorrow."
tool: check_availability {"date": "2024-06-05"} -> failed: "that date is in the past"
```

The model supplied **June 2024** — its training-data present. Every relative date
a real caller uses was unresolvable, so the agent could not book anything at all.

The past-date guard held: it rejected both rather than inventing availability, and
the agent told the caller it didn't know what day it was rather than bluffing.
Correct behaviour on an input nobody had designed for — but the agent was useless.

Fixed by `build_instructions(today)` in `appointment_agent.py`, anchored to the
same `date` object the calendar uses so the prompt and the past-date guard cannot
drift apart. Verified on the next call: `check_availability {"date": "2026-07-27"}`
→ `ok`. Later calls resolved *"Thursday"* → `2026-07-30` and *"Friday"* →
`2026-07-31` correctly.

**Transferable: an LLM has no clock. If you don't tell it what day it is, it will
confidently pick one, and it will pick wrong.** Regression tests in
`tests/test_livekit_tools.py`, including a year-boundary case.

### 2. It refuses to invent a time slot, under pressure

Unscripted, from the live transcript:

```
agent: "We have openings today at 9 AM and 10 AM. Would either of those work?"
user:  "I think I would want the nine thirty. Can we do nine thirty?"
agent: "The closest we have is 9 AM, not 9:30. Would 9 AM be okay, or 10 AM?"
user:  "How about eleven AM?"
agent: "We don't have anything at 11 AM today, just 9 or 10."
```

Two consecutive pushes for times that do not exist, two refusals, no invention.
This is the property the whole tool runtime exists to protect, and it held
against a caller actively trying to bend it.

Asked about services it had no data for, it also declined to improvise a menu.

### 3. Barge-in works. Its latency is not yet measured — and the first attempt was wrong

Interruption is confirmed: the agent's utterance truncates mid-phrase exactly
where the caller starts, the caller's words arrive complete in the transcript, and
the agent answers the interruption instead of resuming its previous sentence. That
held across every session.

**The latency measurement failed, and the failure is worth recording.** The first
attempt timed the gap between the caller's final STT transcript and the assistant's
truncated `conversation_item_added` line. Three different interruption
configurations (`min_words` = 2, 1, 0) produced offsets of 5 ms, 2 ms and 5 ms.
Configuration changes that produce an identical result are not being measured:
`conversation_item_added` is emitted as bookkeeping when the *user's* turn commits,
so it reveals that speech was truncated but says nothing about when audio stopped.

`_instrument_barge_in()` in `src/agent.py` now hooks `user_state_changed` and
`agent_state_changed` and emits VAD-onset-to-playout-stop directly:

```json
{"event": "agent_stopped_speaking", "barge_in_latency_ms": 412.7}
```

Until a call is run with it, `min_duration=0.4` and `min_words=2` remain reasoned
defaults and are labelled as such everywhere in this README.

### 4. Environment notes

- Interruptions are suppressed for the first 3.00 s of a call (`aec warmup
  active`). Do not test barge-in on the greeting.
- STT transcript delay across ~20 turns: **0.02–0.55 s** (Deepgram nova-3).
- `check_availability` round trip: **0.11–0.17 ms** through the tool runtime.
- OpenAI API billing is separate from a ChatGPT subscription. A key with no credit
  returns `429 insufficient_quota`, which resembles nothing else.

---

## Setup

Requires Python ≥ 3.10 and < 3.15 (`livekit-agents` 1.6.7 `requires_python`).
This project was built and tested on 3.12.8.

```bash
cd livekit-voice-agent

python3.12 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip

# Reproducible: fully pinned transitive lock
./.venv/bin/python -m pip install -r requirements.lock.txt

# or: direct dependencies only
./.venv/bin/python -m pip install -r requirements-dev.txt
```

Then copy the environment template and fill it in:

```bash
cp .env.example .env.local     # .env.local is gitignored
```

`.env.example` contains placeholders only (`REPLACE_ME`). The config layer
treats `REPLACE_ME` as *absent*, so an unedited copy cannot masquerade as a
configured environment.

### Dependency strategy

| File | Purpose |
|---|---|
| `pyproject.toml` | Direct dependencies, pinned with `==` |
| `requirements.txt` | Same pins, for `pip install -r` |
| `requirements-dev.txt` | Adds pytest, pytest-asyncio, ruff, mypy |
| `requirements.lock.txt` | `pip freeze` of the working venv — 101 packages, fully pinned including transitives |

Exact direct versions:

```
livekit-agents[deepgram,openai,silero,turn-detector]==1.6.7
livekit-plugins-deepgram==1.6.7
livekit-plugins-openai==1.6.7
livekit-plugins-silero==1.6.7
livekit-plugins-turn-detector==1.6.7
pydantic==2.13.4
python-dotenv==1.1.1
pytest==8.4.2
pytest-asyncio==1.2.0
ruff==0.14.4
mypy==1.18.2
```

---

## Run

```bash
# Everything that works with no credentials at all:
./.venv/bin/python scripts/smoke.py          # startup + config smoke test
./.venv/bin/python -m pytest                 # 63 tests
./.venv/bin/ruff check src tests scripts
./.venv/bin/ruff format --check src tests scripts
./.venv/bin/mypy

# Requires credentials in .env.local:
./.venv/bin/python src/agent.py console      # terminal, local mic + speakers
./.venv/bin/python src/agent.py dev          # connect to LiveKit; use the browser console
./.venv/bin/python src/agent.py start        # production mode
```

`console` / `dev` / `start` are the three startup modes documented in the
[voice AI quickstart](https://docs.livekit.io/agents/start/voice-ai.md).
`python src/agent.py --help` runs with no credentials and lists them
(`console`, `start`, `dev`, `connect`, `download-files`) — useful for confirming
the module imports cleanly before you have keys.

---

## Architecture

```
                    ┌──────────────────────────────────────────┐
  caller  ◀── WebRTC ──▶│  LiveKit room                        │
  (browser)             └──────────────────┬───────────────────┘
                                           │
                              src/agent.py │  AgentServer + @server.rtc_session
                                           ▼
      ┌────────────────────────────────────────────────────────────────┐
      │ AgentSession                                                   │
      │   stt  deepgram.STT(nova-3)                                    │
      │   llm  openai.LLM(gpt-4.1-mini)                                │
      │   tts  openai.TTS(gpt-4o-mini-tts, ash)                        │
      │   vad  silero.VAD.load(...)          ← prewarmed per process   │
      │   turn_handling  TurnHandlingOptions ← voice_agent/turn_handling│
      │   max_tool_steps=3                                             │
      └───────────────────────────┬────────────────────────────────────┘
                                  │ @function_tool
                                  ▼
      ┌────────────────────────────────────────────────────────────────┐
      │ AppointmentAgent  (voice_agent/appointment_agent.py)           │
      │   THIN ADAPTER ONLY: LiveKit call ──▶ runtime.invoke()         │
      │                      ToolOutcome   ──▶ value or ToolError      │
      └───────────────────────────┬────────────────────────────────────┘
                                  ▼
      ┌────────────────────────────────────────────────────────────────┐
      │ ToolRuntime  (voice_agent/tool_runtime/)   NO LIVEKIT IMPORTS  │
      │   1. size cap  ─▶ 2. Pydantic validate  ─▶ 3. derive key       │
      │   4. ledger.run_once (writes only)  ─▶ 5. bounded retry        │
      │   6. one structured, PII-masked log record                     │
      └───────────────────────────┬────────────────────────────────────┘
                                  ▼
      ┌────────────────────────────────────────────────────────────────┐
      │ Demo adapters (voice_agent/appointments/adapters.py)           │
      │   DemoCalendar · DemoCrm — in-memory, deterministic, no network │
      └────────────────────────────────────────────────────────────────┘
```

### Layout

```
src/
  agent.py                          LiveKit entrypoint (AgentServer)
  voice_agent/
    config.py                       env parsing; never holds a secret value
    turn_handling.py                explicit turn detection + interruption config
    appointment_agent.py            Agent subclass; three @function_tool wrappers
    tool_runtime/                   ← the reusable piece
      runtime.py                    ToolRuntime.invoke — the single entry point
      idempotency.py                IdempotencyLedger protocol + in-memory impl
      retry.py                      RetryPolicy, hard-capped
      sanitize.py                   PII masking for logs
      errors.py                     Transient / Permanent / Validation taxonomy
    appointments/
      schemas.py                    Pydantic argument models (extra="forbid")
      adapters.py                   DemoCalendar, DemoCrm
      registry.py                   wires the three tools into a ToolRuntime
scripts/smoke.py                    credential-free startup + config smoke test
tests/                              63 tests
```

### The tool runtime interface

One registration path and one invocation path. There is no way to execute a
handler on unvalidated arguments, and no way to pass a wrong idempotency key —
the runtime derives it.

```python
runtime = ToolRuntime(ledger=..., retry=RetryPolicy(max_attempts=3), on_record=...)

runtime.register(ToolSpec(
    name="book_appointment",
    args_model=BookAppointmentArgs,   # Pydantic, extra="forbid"
    handler=book_appointment,         # async (args) -> result
    once_per_turn=True,               # writes: ledger-guarded. reads: False
))

outcome = await runtime.invoke("book_appointment", turn_id=..., raw_args={...})
# outcome.status ∈ {"ok", "replayed", "invalid", "failed"}
```

`invoke` never raises for an expected failure — every path returns a
`ToolOutcome`, so a caller cannot forget a `try`. The only exception it raises is
`KeyError` for an unregistered tool name, which is a programming error.

**Idempotency key** = `sha256(tool | turn_id | canonical validated args)`,
truncated, prefixed `tk_`. `turn_id` comes from `RunContext.speech_handle.id`,
which is stable across the whole agent turn — so two tool calls the model emits
while producing one reply collide and execute once. `function_call.call_id` would
be unique per emission and would therefore guard nothing.

**Two independent guards, defending different things.** The ledger stops a
*duplicate* of the same request. `DemoCalendar.book` separately raises
`PermanentToolError("that time was already taken")` for a *conflicting* request
from a later turn. Both are tested.

**Failures are not cached.** A `factory` that raises leaves the key reusable — one
transient outage must not permanently block that caller from booking that slot.

**Swapping the ledger.** `IdempotencyLedger` is a single-method `Protocol`
(`run_once`). The in-memory implementation is process-local and bounded to 1024
entries. For multi-worker production, implement the same protocol over Redis
(`SET NX` + result blob) or a Postgres table keyed on the idempotency key; the
runtime does not change.

---

## Barge-in

### Configuration shipped

From `voice_agent/turn_handling.py`, verified reaching `AgentSession` in
`tests/test_barge_in.py`:

```python
{
  "turn_detection": "vad",
  "endpointing":  {"mode": "fixed", "min_delay": 0.4, "max_delay": 3.0},
  "interruption": {"enabled": True,
                   "mode": "vad",
                   "discard_audio_if_uninterruptible": True,
                   "min_duration": 0.4,
                   "min_words": 2,
                   "false_interruption_timeout": 2.0,
                   "resume_false_interruption": True},
}
```

Silero VAD (`silero.VAD.load`): `min_speech_duration=0.05`,
`min_silence_duration=0.4`, `prefix_padding_duration=0.5`,
`activation_threshold=0.5`.

Every value is set explicitly, including ones matching the framework default. On
a receptionist call the defaults are not self-evidently right, and an inherited
default is invisible in code review.

### Why these values

- **`min_duration=0.4` + `min_words=2` together.** Energy alone is a bad
  interruption trigger — a cough, a door, a passing truck all clear a VAD gate.
  Requiring two transcribed words means the agent yields to *speech*, not noise.
  This is the direct counterpart of Vapi's `numWordsToInterruptAssistant`.
- **`endpointing.min_delay=0.4` rather than the 0.5 default.** A booking call is
  full of short clipped answers ("yeah", "ten's fine"). Half a second of dead air
  after each one reads as sluggish. 0.4s is roughly the floor before genuine
  mid-sentence pauses start getting cut off.
- **`resume_false_interruption=True` with a 2.0s timeout.** If the "interruption"
  produced no transcript, it wasn't one. Resuming beats leaving the caller in
  silence wondering whether the agent died.
- **`discard_audio_if_uninterruptible=True`.** Prevents the agent replaying a
  stale buffered chunk after it yields.
- **`turn_detection="vad"` rather than the framework-default audio turn
  detector.** The audio turn detector is generally better at "the caller paused
  mid-sentence", but it runs through LiveKit Inference — a credentialed
  dependency. This project pins the self-contained option so the pipeline is
  Deepgram + OpenAI + Silero and nothing else. To switch, set
  `turn_detection=inference.TurnDetector()`; that path is **not verified here**.

### What the automated barge-in tests do and do not prove

**They prove** (`tests/test_barge_in.py`, 9 tests, all passing):

1. The configuration above is what reaches `AgentSession`, and `AgentSession`
   accepts it. This catches the failure that costs an afternoon: a mistyped key
   that the framework silently ignores, so the setting never applies.
2. LiveKit's **interruption control path** works end to end against a real
   `AgentSession`: an in-flight utterance stops, `SpeechHandle.interrupted`
   becomes `True`, the audio sink is told to drop its buffer, and playout stops
   early. Measured: ~0.9s of a 4.0s utterance played before the interrupt landed.
3. A **control case**: without an interrupt, the same utterance drains fully. So
   "stopped early" is a real difference, not an artifact of the harness.
4. Speech marked **uninterruptible** — what a mutating tool call runs under, via
   `context.disallow_interruptions()` — survives a normal interrupt (LiveKit
   raises `RuntimeError` rather than silently ignoring it) and yields only to
   `session.interrupt(force=True)`.

**They do not prove** anything acoustic. There is no microphone, no real speech,
no Silero VAD scoring, no Deepgram transcription, no WebRTC. The trigger is a
direct `session.interrupt()` call, not detected user speech. Whether
`min_duration=0.4` and `min_words=2` are the right thresholds against a real
caller on a real speakerphone is a question these tests cannot answer. That is
what the [manual acceptance script](#manual-acceptance-script-browser-call) is
for, and **it has not been run.**

The test harness fakes only the two edges of the audio path — a TTS that emits
silent PCM, and a speaker that buffers and drains at wall-clock speed. Everything
between them is LiveKit's own code.

### Comparison to the known Vapi failure mode — not yet made

The intent was to compare LiveKit's barge-in behaviour against a known
production failure mode on Vapi (false endpointing on certain handsets, resolved
there by an STT swap and by disabling background denoising). **That comparison
requires both stacks on a live call and has not been made.** Stating a conclusion
here without having heard both would be exactly the kind of claim this project
exists to be able to back up.

---

## LiveKit vs Vapi

Drawn from building on both. The Vapi column reflects a production
outbound-calling integration (`MITS_CRM`); the LiveKit column reflects this repo
plus its documentation.

| | Vapi | LiveKit Agents |
|---|---|---|
| Runtime ownership | Vapi owns the session, turn detection, audio path | You own it, in Python asyncio |
| Configuration surface | Assistant JSON: `numWordsToInterruptAssistant`, `responseDelaySeconds`, `backgroundDenoisingEnabled`, `silenceTimeoutSeconds` | Code: `TurnHandlingOptions`, VAD parameters, session events, custom nodes |
| Barge-in tuning | Provider-defined knobs; behaviour changes when the platform changes | Explicit and versioned with your code; unit-testable, as here |
| Tool calls | Webhook out to your server | In-process Python function |
| Idempotency | Your responsibility (signed webhooks + ledger) | Your responsibility — hence `tool_runtime/` |
| Time to first working call | Minutes | Hours |
| Ops burden | Vapi runs it | You deploy and scale workers |
| Cost shape | Per-minute platform fee on top of model costs (~$0.09–0.11/min observed) | Model + transport costs; no platform margin on self-hosted |
| Debuggability | Dashboard, transcripts, provider-side logs | Whatever you instrument — full process access |

**When each wins.** Vapi is the right call when the goal is a working phone agent
this week and the conversation shape is standard. LiveKit wins when you need to
own turn-taking, instrument per-turn latency, run custom logic inside the audio
loop, avoid a per-minute platform fee at volume, or satisfy a buyer who
explicitly wants the runtime owned rather than rented.

They are not mutually exclusive; the tool runtime in this repo is deliberately
transport-agnostic and would sit behind a Vapi webhook handler unchanged.

---

## Security

### Threat model

Trust boundaries, in order of how much they matter here:

| Boundary | Threat | Mitigation |
|---|---|---|
| **Caller speech → STT → LLM** | Prompt injection. A caller can say anything, and it lands in the context window. The system prompt is *not* a security boundary. | Only three tools exist. Every argument is Pydantic-validated with `extra="forbid"`. Enforcement is in code, not in the prompt. |
| **LLM → tool arguments** (OWASP LLM05, *Improper Output Handling*) | Model output treated as trustworthy: oversized payloads, invented arguments, malformed values. | 4 KB raw-payload cap before parsing; strict schemas; unknown fields rejected rather than dropped. No `eval`, no SQL, no shell, no filesystem, no outbound HTTP anywhere in the tool path. |
| **Tool execution** (OWASP LLM06, *Excessive Agency*) | Double-booking, runaway tool loops. | `once_per_turn` ledger guard on every write; `max_tool_steps=3` on the session; `RetryPolicy` hard-capped at 5 attempts / 10s delay / 30s timeout, validated in `__post_init__`. |
| **Tool errors → LLM / transcript** | Internal details (DSNs, credentials, stack context) leaking into what the agent says out loud. | Unexpected exceptions are logged locally and returned to the model as `"internal error"`. Validation errors report **field names and reason codes only** — never the offending value. Tested. |
| **Logs** | Caller PII (name, phone, email) shipped to a log aggregator. | `sanitize_args` masks on a broad substring match (`email`, `phone`, `name`, `address`, `note`, `token`, `secret`, `key`, `ssn`, `dob`, …) and truncates at 200 chars. Applied inside the runtime, so a new log sink cannot start receiving raw PII. Tested. |
| **Secrets** | Credentials in source, in a repr, in a crash dump. | `Settings` **never stores a secret value** — only which credential *names* were present. Plugins read their own keys from the environment. `.env.example` has placeholders only; `.env`, `.env.local`, `*.pem`, `*.key` are gitignored. Tested. |
| **Unbounded consumption** (OWASP LLM10) | Cost and dead air from retry storms. | Bounded retries with capped exponential backoff; `asyncio.wait_for` timeout per attempt; ledger bounded to 1024 entries with oldest-first eviction. |

### Deliberately not addressed

Out of scope for a four-hour capability build, and named rather than hidden:

- **No authentication or authorization.** Anyone who reaches the room talks to
  the agent. A real deployment needs token-scoped room access.
- **No rate limiting** on tool invocation beyond the retry cap.
- **No durable audit trail.** Invocation records go to `logging`, not to storage.
- **No TCPA / DNC / consent gating.** This is an *inbound* demo. Outbound calling
  needs all of it enforced in code.
- **In-memory ledger is process-local.** Two workers do not share it.
- **Jitter is off by default** (`RetryPolicy(jitter=0.0)`) so tests can assert an
  exact backoff schedule. Turn it on in production to avoid synchronised retries.

---

## Manual acceptance script (browser call)

**Steps 0–4 run on 2026-07-27** — results in
[Live findings](#live-findings-2026-07-27) and, turn by turn, in
[`docs/acceptance-findings.md`](docs/acceptance-findings.md). **Steps 5–11 are
still unrun.** Record answers in that file as you go; the Barge-in section gets
rewritten from it, not from memory.

### Prerequisites

- LiveKit Cloud project (free tier) → `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`
- `DEEPGRAM_API_KEY`, `OPENAI_API_KEY`
- All five in `.env.local`
- Optional: LiveKit CLI (`brew install livekit-cli`, then `lk cloud auth`) for
  `lk agent dev` and the Agent Console

### Steps

**0. Preflight (no credentials needed).**
```bash
./.venv/bin/python scripts/smoke.py
```
Expect `SMOKE PASSED`. With `.env.local` filled in, section `[6]` should print
`All credentials present`. **Confirmed 2026-07-27:** `credentials_missing: []`,
`livekit-agents==1.6.7`, Silero VAD loaded in 24 ms, all three tools registered,
tool-runtime round-trip = 1 booking from 2 calls with bad input rejected.

Note: the trailing `SMOKE PASSED — the agent starts and its config is valid without
credentials` is a fixed string in the script. It is not a claim that section `[6]`
was empty; read `[6]` itself.

**1. Start the agent.**
```bash
./.venv/bin/python src/agent.py dev
```
Expect: `registered worker` in the log, and no exception. Open the
[Agent Console](https://docs.livekit.io/agents/start/console.md) from the LiveKit
Cloud dashboard, set **Agent name** to `appointment-agent`, and start a session.

**2. Greeting.** The agent should speak first, in one short sentence, mentioning
that you can interrupt it. ✅ / ❌

**3. Availability, no hallucinated slots.** Pick the **next weekday after today**
— the demo calendar opens Mon–Fri only and refuses any date in the past — and ask
for it the way a caller would: *"What have you got on [that weekday] the
[date]?"* Expect a `check_availability` tool call in the console's tool pane, and
spoken times drawn from the fixed business hours `09:00 / 10:00 / 13:00 / 15:00`
— nothing else. ✅ / ❌

**4. Barge-in, mid-sentence — the one that matters.** Ask an open question
(*"tell me about your appointment types"*). While the agent is **mid-word**, say
*"actually hold on"*. Record:
   - Did audio stop within roughly 300–500 ms? ☐
   - Did it stop mid-word, or finish the sentence first? ☐
   - Did your interrupting words appear in the transcript, complete? ☐
   - Did the agent respond to what you said, or resume its old answer? ☐

**5. False-positive rejection.** While the agent speaks, cough once, or say a
single *"mm-hm"*. Expected: it keeps talking (`min_words=2` should filter this).
If it yields, `min_words` is too low for your audio path. ☐

**6. False-interruption recovery.** While the agent speaks, tap the mic or make a
short non-speech noise loud enough to trip VAD. Expected: brief pause, then it
resumes where it left off within ~2s (`false_interruption_timeout=2.0`,
`resume_false_interruption=True`). ☐

**7. Booking, with confirmation.** Pick a time. Give a name and number. Expect
the agent to read the number back digit by digit before booking. Confirm.
Expect exactly **one** `book_appointment` call in the tool pane. ☐

**8. Double-fire guard.** Immediately say *"sorry, book that again"*. Expected:
the calendar still shows one booking. If the model re-calls the tool within the
same turn it returns `already_recorded: true`; from a *later* turn the calendar
rejects it with "that time was already taken". Either way: one booking. ☐

**9. Uninterruptible write.** While the agent is confirming a booking, talk over
it. Expected: it does not abandon the write mid-flight
(`context.disallow_interruptions()`). ☐

**10. Lead capture.** Start a fresh call, say you're just gathering information.
Expect a `capture_lead` call, not a booking. ☐

**11. Log check.** In the agent's stdout, find the `tool_invocation` JSON lines.
Verify each has `turn_id`, `tool`, `idempotency_key` (prefix `tk_`), `status`,
`attempts`, `latency_ms` — and that **no raw phone number, email or full name
appears anywhere**. ☐

**12. Record it.** Screen + system audio + microphone. Keep step 4 in one
unbroken take — a cut there destroys the only thing the recording proves.

### Tuning, if step 4 disappoints

| Symptom | Change |
|---|---|
| Yields to coughs and background noise | `INTERRUPTION_MIN_WORDS=3`, or `INTERRUPTION_MIN_DURATION=0.5` |
| Slow to yield when you genuinely interrupt | `INTERRUPTION_MIN_DURATION=0.25`, `INTERRUPTION_MIN_WORDS=1` |
| Cuts you off mid-sentence when you pause to think | `ENDPOINTING_MIN_DELAY=0.6` |
| Sluggish after short answers | `ENDPOINTING_MIN_DELAY=0.3` |
| Interrupting words truncated in the transcript | Raise `prefix_padding_duration` in `build_silero_vad_kwargs()` |

The first four rows change `INTERRUPTION_MIN_WORDS`, `INTERRUPTION_MIN_DURATION`
and `ENDPOINTING_MIN_DELAY` — all environment variables, so no code change and no
redeploy. The last row is the exception: `prefix_padding_duration` is currently
hard-coded in `build_silero_vad_kwargs()` and needs an edit and a restart.

---

## Verification

Every command below was run in this repo. Output is verbatim.

```
$ ./.venv/bin/python -m pytest -q
64 passed in 6.31s

$ ./.venv/bin/ruff check src tests scripts
All checks passed!

$ ./.venv/bin/ruff format --check src tests scripts
26 files already formatted

$ ./.venv/bin/mypy
Success: no issues found in 16 source files

$ ./.venv/bin/python -m compileall -q src tests scripts
(exit 0)

$ ./.venv/bin/python -m pip check
No broken requirements found.

$ ./.venv/bin/python scripts/smoke.py
SMOKE PASSED — the agent starts and its config is valid without credentials.
(exit 0)
```

`mypy` runs in `strict` mode over `src/` and `scripts/` against LiveKit's real
type information — `livekit-agents` and its plugins ship `py.typed`, so their
types are used rather than skipped. **There are no `# type: ignore` comments in
`src/` or `scripts/`.** (Three exist in `tests/`, where test doubles stand in for
`rtc.AudioFrame` and `RunContext` without importing their types.)

### Test coverage by area

| File | Tests | Covers |
|---|---|---|
| `test_idempotency.py` | 6 | duplicate key executes once; concurrent duplicates; turn separation; reads not guarded; failures don't poison the key |
| `test_validation.py` | 6 | missing/out-of-range/unknown fields; oversized payload; unregistered tool; error text doesn't echo input |
| `test_retry.py` | 7 | transient retried then succeeds; bounded at `max_attempts`; backoff capped; permanent not retried; unexpected exception not retried and not leaked; timeout treated as transient; unbounded config rejected |
| `test_invocation_log.py` | 6 | record fields; PII masked; JSON-serializable; failure recorded; sanitizer behaviour |
| `test_appointment_tools.py` | 11 | the three tools; determinism; weekend/past-date rules; double-book prevention (both guards); sanitized records |
| `test_livekit_tools.py` | 9 | wrapper → runtime plumbing; turn id from `speech_handle`; `disallow_interruptions` on writes; `ToolError` mapping; no data leak in errors |
| `test_barge_in.py` | 9 | interruption config; `AgentSession` accepts it; interrupt stops speech; uninterrupted control case; uninterruptible speech; forced interrupt |
| `test_config.py` | 9 | defaults; `REPLACE_ME` ≠ credential; secrets never stored; validation; credential-free startup |

### What works with no credentials

- Full test suite, ruff, mypy, compileall, pip check
- `scripts/smoke.py`: imports, plugin resolution, settings parsing, **Silero VAD
  model loads locally in ~54 ms**, `AgentSession` accepts the turn-handling
  config, agent + 3 tools register, and a real tool round-trip through the
  runtime (4 slots found → 1 booking from 2 calls → bad input rejected)
- `python src/agent.py --help`

### What does not

Anything that joins a room or calls a model provider: `console`, `dev`, `start`.

---

## Official sources

Every framework-specific decision traces to one of these. All fetched
2026-07-27, and all correspond to the installed `livekit-agents==1.6.7`.

| Used for | URL |
|---|---|
| `AgentServer`, `@server.rtc_session`, `agents.cli.run_app`, startup modes, full quickstart agent | https://docs.livekit.io/agents/start/voice-ai.md |
| `TurnHandlingOptions`, `EndpointingOptions`, `InterruptionOptions` — every parameter and default | https://docs.livekit.io/reference/agents/turn-handling-options.md |
| Turn detection modes (`stt` / `vad` / `realtime_llm` / `manual`), interruptions, false interruptions | https://docs.livekit.io/agents/logic/turns.md |
| Silero VAD plugin | https://docs.livekit.io/agents/logic/turns/vad.md |
| Adaptive interruption handling, turn-boundary cooldown | https://docs.livekit.io/agents/logic/turns/adaptive-interruption-handling.md |
| Turn-taking tuning guidance | https://docs.livekit.io/agents/logic/turns/tuning.md |
| `@function_tool`, `RunContext`, `ToolError`, `context.disallow_interruptions()` for mutating calls | https://docs.livekit.io/agents/logic/tools/definition.md |
| `AgentSession` lifecycle | https://docs.livekit.io/agents/logic/sessions.md |
| Test framework (`session.run`, `mock_tools`, judges) | https://docs.livekit.io/agents/start/testing/test-framework.md |
| Agent Console (browser) | https://docs.livekit.io/agents/start/console.md |
| Docs index used to locate the above | https://docs.livekit.io/llms.txt |
| Version, release date, `requires_python`, extras | https://pypi.org/project/livekit-agents/ |

### Delta from the original build plan

The plan was written against an older API shape. Three corrections, all resolved
by reading current docs and introspecting the installed package:

1. **`AgentServer` + `@server.rtc_session`, not `WorkerOptions(entrypoint_fnc=…)`.**
   The PyPI project page for 1.6.7 still shows the older `WorkerOptions` example.
   The installed package exposes **both**; the current docs use `AgentServer`, so
   this project does too. Verified by introspection: `livekit.agents` 1.6.7 has
   `AgentServer`, `TurnHandlingOptions`, `inference`, *and* the legacy
   `WorkerOptions` and `RoomInputOptions`.
2. **Interruption settings live under `turn_handling=TurnHandlingOptions(...)`.**
   The flat `AgentSession(min_interruption_duration=…, allow_interruptions=…)`
   kwargs still exist on the constructor, but the documented form is the nested
   one, and it is what this project uses.
3. **The browser entry point is the Agent Console** (`lk agent dev` → Console in
   the LiveKit Cloud dashboard), which is what the current docs describe. The
   plan's "Agents Playground" name does not appear in current documentation.

Deliberately **not** built, per the plan's hard stops: telephony origination,
Temporal, OpenTelemetry, multi-agent routing, policy engine, human handoff,
Redis, database, frontend, deployment.

---

## Blocked / not verified

Honest list. None of these are hidden elsewhere in this README.

1. **Barge-in latency in milliseconds.** Interruption is confirmed to work; how
   fast is unmeasured. `min_duration=0.4` and `min_words=2` remain reasoned
   defaults. `_instrument_barge_in()` makes this a three-minute call to settle —
   see [finding 3](#3-barge-in-works-its-latency-is-not-yet-measured--and-the-first-attempt-was-wrong).
2. **False-positive rejection (cough, "mm-hm") not observed.** Attempted, but no
   corresponding event appeared in the log, so it is scored neither pass nor fail.
3. **Booking not completed end to end on a live call.** `check_availability` ran
   live and correctly; `book_appointment` and `capture_lead` are covered by
   automated tests only. Steps 7–11 of the manual script remain unrun.
4. **No recorded demo cut.** A screen recording of a live call exists; it has not
   been edited into a demo.
5. **No Vapi-vs-LiveKit barge-in comparison.** Needs both stacks on a live call.
6. **LiveKit audio turn detector not exercised.** `turn_detection="vad"` is what
   ships and what is tested. `inference.TurnDetector()` needs LiveKit Inference
   credentials.
8. **LiveKit's own test framework not used.** `session.run(user_input=…)` and the
   `judge()` helpers require an LLM instance, so they need credentials. The tests
   here drive `AgentSession` directly instead.

### Next, in order

1. One call with `_instrument_barge_in()` running → real latency numbers →
   replace the reasoned defaults in [Barge-in](#barge-in).
2. Steps 7–11 of the manual script: booking, double-fire guard, lead capture,
   log check.
3. Cut the recorded call into a demo.

---

## Rollback

```bash
rm -rf /Users/jeanhyacinthe/Claude_Projects/Revenue_Sprint/livekit-voice-agent
```

Self-contained. Nothing outside this directory was created or modified, no
system packages were installed, and no external service was contacted beyond
PyPI and the LiveKit documentation site.
