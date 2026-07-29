# Acceptance call — findings

Fill this in DURING the call. It is the raw record; the README's Barge-in
section gets rewritten from it afterwards. Empty boxes are honest. Guessed
numbers are not.

Date: ____________  Audio path: ☐ MacBook mic  ☐ headset  ☐ other: ________

---

## 2 — Greeting
Spoke first, one sentence, mentioned interruption?   ☐ yes  ☐ no
Verbatim: ______________________________________________

## 3 — Availability
`check_availability` appeared in the tool pane?      ☐ yes  ☐ no
Times spoken: ___________________________________________
Any time NOT in 09:00 / 10:00 / 13:00 / 15:00?       ☐ none  ☐ yes: ________

## 4 — BARGE-IN (the one that matters) — keep recording unbroken
Question asked: _________________________________________
Interrupted with: "actually hold on"

a. Audio stopped in roughly:  ☐ <300ms  ☐ 300–500ms  ☐ 0.5–1s  ☐ >1s
b. Stopped:                   ☐ mid-word  ☐ finished the word  ☐ finished the sentence
c. My words in transcript:    ☐ complete  ☐ first word clipped  ☐ garbled
d. Agent then:                ☐ answered what I said  ☐ resumed its old answer  ☐ confused

Notes: __________________________________________________

## 5 — Cough / "mm-hm"  (tests min_words=2)
Agent:  ☐ kept talking (pass)  ☐ yielded (min_words too low)

## 6 — Noise, non-speech  (tests false_interruption_timeout=2.0)
☐ paused then resumed within ~2s (pass)   ☐ resumed later: ____s   ☐ never resumed

## 7 — Booking
Read the number back digit by digit?   ☐ yes  ☐ no
`book_appointment` calls in tool pane:  ______ (expect exactly 1)

## 8 — "book that again"
Bookings on the calendar after:  ______ (expect 1)
Response seen:  ☐ already_recorded: true  ☐ "that time was already taken"  ☐ other

## 9 — Talk over the confirmation
Abandoned the write?  ☐ no (pass)  ☐ yes

## 10 — Lead capture, fresh call
`capture_lead` called instead of a booking?  ☐ yes  ☐ no

## 11 — Log check
Every `tool_invocation` line has turn_id / tool / idempotency_key(tk_) /
status / attempts / latency_ms?   ☐ yes  ☐ no — missing: ____________
Any raw phone / email / full name in the logs?  ☐ none (pass)  ☐ found: ________

## 12 — Recording
File: ___________________________  Step 4 unbroken?  ☐ yes  ☐ re-shoot

---

## If step 4 disappointed — what I changed and re-tested
| Change | New result |
|---|---|
|  |  |

---

# Run 1 — 2026-07-27, ~00:41 (unrecorded shakedown)

Not the recorded take. Logged here because it is the first time the agent
carried live audio, and because it produced findings that changed the code.

## Confirmed working, from the log

| Step | Result | Evidence |
|---|---|---|
| 2 Greeting | PASS | *"Hello, I can check our schedule and book an appointment for you—just jump in whenever you like. How can I help?"* — one sentence, invites interruption |
| 3 Availability | PASS | `check_availability {"date": "2026-07-27"}` → `ok`, 1 attempt, 0.11 ms |
| 3 No invented slots | **PASS, under pressure** | Caller pushed for 9:30 → *"The closest we have is 9 AM, not 9:30."* Then pushed for 11 AM → *"We don't have anything at 11 AM today, just 9 or 10."* Refused twice rather than inventing |
| 4 Barge-in | Works, not yet measured | Agent's line truncates mid-sentence at *"…for your dental"* exactly where the caller starts. Needs the timed take |
| STT | PASS | transcript delay 0.17–0.27 s across every turn |

Not reached: 7 booking, 8 double-fire, 9 uninterruptible write, 10 lead capture,
11 log check. Call ended before name/number were given.

The three `ERROR livekit ... data channel closed unexpectedly` lines are session
teardown when End session is clicked. Not a defect.

## Finding that changed the code — no date anchor

Before the fix, with no date in the system prompt:

```
user: "Need an appointment for dental."
tool: check_availability {"date": "2024-06-03"} → failed: "that date is in the past"
user: "Yeah. Tomorrow."
tool: check_availability {"date": "2024-06-05"} → failed: "that date is in the past"
```

The model supplied June 2024 — its training-data present. Every relative date a
real caller uses ("today", "tomorrow", "next Tuesday") was unresolvable, so the
agent could not book anything at all.

The past-date guard held: it rejected both, and the agent told the caller it did
not know what day it was rather than bluffing. Correct behaviour on an input
nobody had anticipated.

Fixed by `build_instructions(today)` in `appointment_agent.py`, anchored to the
same `date` object the calendar uses. Regression tests in
`tests/test_livekit_tools.py`. Verified live in run 1: the very next call passed
`2026-07-27`.

**Transferable lesson for any voice agent: an LLM has no clock. If you do not tell
it what day it is, it will confidently pick one — and it will pick wrong.**

## Environment notes for the next run

- Interruptions are suppressed for the first 3.00 s (`aec warmup active`). Do not
  attempt step 4 on the greeting; it will record a false failure.
- OpenAI API billing is separate from ChatGPT Plus. A key with no credit returns
  `429 insufficient_quota`, which looks nothing like an auth problem.

---

# Run 2 — 2026-07-27, 11:35 (instrumented)

`_instrument_barge_in()` live. Config: `min_words=2`, `min_duration=0.4` (shipping).

## Step 4 — BARGE-IN, MEASURED

VAD onset → playout stop, direct from the event pair. Not inferred.

| # | ms |
|---|---|
| 1 | 349.2 |
| 2 | 849.3 |
| 3 | 950.9 |
| 4 | 1000.0 |

n=4 · min 349 · median 900 · max 1000 · mean 787.

a. Audio stopped in:      ☐ <300ms  ☑ 349ms once  ☑ 850–1000ms typical
b. Stopped mid-word:      ☑ yes — assistant text truncates mid-phrase
c. Words in transcript:   ☑ complete
d. Agent then:            ☑ answered the interruption, never resumed its old line

5 of 9 `agent_stopped_speaking` events logged `barge_in_latency_ms: null` — the agent
finishing its own sentence. The discrimination works; it is not labelling every stop a
barge-in.

## Step 5 — cough / "mm-hm"
☐ pass ☐ fail — **NOT OBSERVED AGAIN.** Second attempt, second time nothing reached
the log. Either the cough never tripped VAD, or it tripped and was filtered without
emitting an event. Needs a deliberate, loud, ≥0.5s non-speech noise while the agent is
mid-sentence, with the log watched live.

## Step 3 — date handling, third confirmation
- "Today, Monday, we have openings at 9 in the morning or 10 in the morning."
- Asked for Wednesday → "On Wednesday, we have openings at 9 in the morning or 10..."
- Pushed for 9:30 → "I don't see a 9:30 opening on Wednesday, but we do have 9:00 or
  10:00." **Refused to invent, again.**

## Step 7 — booking: PARTIAL
Agent asked: *"Can I have the phone number you want to use for the booking? I will read
it back to..."* — correct behaviour, read-back promised. Caller declined to give one, so
**`book_appointment` still has never executed on a live call.** This is now the single
largest untested path.

## Step 10 — lead capture: PARTIAL
On decline the agent offered: *"If you want to book later, I can take your name and
phone number to have someone follow up with you."* Correct instinct — but no
`capture_lead` tool call, because the caller declined that too.

---

# Run 3 — 2026-07-28, 15:05–15:07 EDT (the booking run)

Log: `logs/agent_run3.log` (45,563 bytes) · Recording: `~/Desktop/Screen Recording 2026-07-28 at 3.04.59 PM.mov` (178 MB)
Config unchanged from run 2: `min_words=2`, `min_duration=0.4`, `false_interruption_timeout=2.0`.

A first session (`console-0fe8e9f4`, 15:04:41) was abandoned 1.4 s into the greeting —
participant disconnect, not a defect. The run below is `console-57f3c5e6`.

## ✅ Step 7 — BOOKING: `book_appointment` EXECUTED ON LIVE AUDIO

The path that had never run. Verbatim from the log:

```json
{"event": "tool_invocation", "timestamp": 1785265615.125752,
 "turn_id": "speech_86ac621242b7", "tool": "book_appointment",
 "idempotency_key": "tk_b0a20ca7f536a5ba948dd0dabdc1b3cd",
 "args": {"slot_id": "2026-07-29T13:00", "caller_name": "J***[redacted]",
          "phone": "******5555", "email": null, "reason": "d***[redacted]"},
 "status": "ok", "attempts": 1, "latency_ms": 0.31, "error": null}
```

One call, `ok`, first attempt, 0.31 ms. **Capability gap 2 is closed.**

Supporting behaviour in the same exchange:

| Check | Result | Evidence |
|---|---|---|
| Read-back before writing | PASS (grouped, not digit-by-digit) | *"To confirm, your phone number is 305-555-5555? Is that correct?"* — the write fired only after *"Yes. That's right."* |
| Asked for digits explicitly | PASS | *"Could you please say your phone number, digit by digit, so I can make sure I have it right?"* |
| Digits assembled from fragments | PASS | STT committed the number as **three separate turns** (`"Three zero five."` / `"Five five five,"` / `"five five five five."`). The LLM reassembled all 10 digits correctly. |
| Date anchoring | PASS (4th confirmation) | *"tomorrow"* → `check_availability {"date": "2026-07-29"}` |
| No invented slots | PASS (4th confirmation) | Caller pushed for 1:30 PM → *"We don't have a 1:30 PM slot available tomorrow. Would you prefer 1 PM"* |

## 🔴 NEW DEFECT 1 — hallucinated fulfilment: the agent confirmed a constraint it never recorded

The caller said **"I need a female doctor."** The agent replied *"Thanks for letting me know
about the female doctor preference"* and, after the booking, confirmed:

> **"Your dental appointment is set for 1 PM tomorrow with a female doctor."**

Nothing in the system recorded that preference. The tool args carried
`reason: "dental appointment"` and nothing else. `BookAppointmentArgs` has
`extra="forbid"`, so the model **could not** have passed it even if it tried — the schema
did its job. The failure is downstream: **the agent verbally committed to a constraint the
write does not contain.**

Severity: this is the highest-severity finding in any run so far. The date-anchor bug (run 1)
produced a *visible* failure — the agent could not book. This one produces a *silent* one: the
caller hangs up satisfied and arrives to a booking that does not match what they were promised.
A real clinic gets a complaint; a real client gets a liability.

Two candidate fixes, unbuilt pending a decision:

- **(a) Instruction guard** — forbid confirming any detail that is not present in the tool
  result. Smallest change, addresses the safety failure directly.
- **(b) Schema field** — add a bounded `preferences` / `notes` field to `BookAppointmentArgs`
  so constraints can actually be captured. Addresses the product gap, widens the trust boundary.

(a) is the fix. (b) is a feature. Doing (b) alone would not prevent the next hallucinated
confirmation about something *else*.

## 🔴 NEW DEFECT 2 — step 11 FAILS: raw PII reaches the log file

**The project's own sanitizer works.** `sanitize_args` masked every sensitive field:
`caller_name → "J***[redacted]"`, `phone → "******5555"`, `reason → "d***[redacted]"`.
Because the caller used a real name rather than the scripted synthetic one, this is now
verified against **real** PII, which is stronger evidence than the synthetic test would have been.

**But the sanitizer is not the only writer to that file.** 2 ms earlier, LiveKit's own
framework logger emitted the unredacted arguments:

```
15:06:55,123 - DEBUG livekit.agents - executing tool {"function": "book_appointment",
  "arguments": "{\"slot_id\": \"2026-07-29T13:00\", \"caller_name\": \"<caller's real name>\",
  \"phone\": \"3055555555\", \"email\": null, \"reason\": \"dental appointment\"}"
```

`sanitize.py`'s docstring claims sanitizing at the runtime rather than the sink means "a new log
destination cannot accidentally start receiving raw PII." That claim is correct about *this
project's* log lines and **wrong about the log file as a whole**. Any aggregator tailing this
file gets raw caller PII from the framework's DEBUG line.

Unverified mitigation: `dev` raises the root level to DEBUG and `start` is thought to run at INFO,
which would suppress it in production. **That has not been tested and must not be claimed.** The
explicit fix is to pin `logging.getLogger("livekit.agents").setLevel(logging.INFO)` or attach a
redacting filter, so the guarantee does not depend on which CLI verb was typed.

## ⚠️ NEW DEFECT 3 — instrumentation artifact: a 9.9 ms "barge-in"

```
15:06:04,891  user_started_speaking
15:06:04,901  agent_stopped_speaking   barge_in_latency_ms: 9.9
```

9.9 ms is below any physically plausible playout-stop latency — the agent finished its own
sentence (*"…Do any of these work for you?"*) as VAD onset happened to fire. The
`_instrument_barge_in` discrimination rule (*"only a stop that follows a user onset is a
barge-in"*) is necessary but not sufficient: coincidence defeats it.

Suggested guard: discard any measured overlap below a floor (~100 ms) as a coincident stop
rather than an interruption. **Excluded from the statistics below.**

## Step 4 — barge-in, three new valid samples

| Run 3 # | ms |
|---|---|
| 1 | 351.5 |
| 2 | 1100.2 |
| 3 | 1100.3 |
| — | ~~9.9~~ excluded, see defect 3 |

Pooled with run 2 (identical config, so pooling is legitimate):

**n=7 · 349.2 · 351.5 · 849.3 · 950.9 · 1000.0 · 1100.2 · 1100.3**
**min 349 · median 951 · max 1100 · mean 814**

⚠️ **The README's "349–1000 ms, median 900 ms (n=4)" is now stale and its upper bound is
understated.** Run 3 produced two samples above the previously claimed ceiling. Corrected
figure: **349–1100 ms, median 951 ms, n=7.**

## Not reached this run

| Step | Status |
|---|---|
| 5 — cough / non-speech | **Still not observed.** Third consecutive run. |
| 8 — double-fire | Not attempted; session ended after the booking |
| 9 — talk over the confirmation | Not attempted |
| 10 — `capture_lead` | Not attempted — still never executed live |

## Note on protocol

The run-3 script specified a synthetic caller (Marcus Webb / 305-555-0142 / 9:00 AM). The actual
call used the caller's own real name, 305-555-5555, and the 1:00 PM slot. Recorded here because it
changes what the evidence shows: the PII masking result is **stronger** than planned (verified
against a real name), and the "female doctor" exchange — which produced defect 1 — was
improvised and would not have occurred under the script. Deviating from the script found the
most serious bug in the project. Worth remembering before over-scripting run 4.


### A note on this file's own redactions

The caller's real name is replaced with `<caller's real name>` throughout, including
inside the quoted leak. Publishing the verbatim leak in the document that reports the
leak would have been a neat way to commit the same error twice.

`305-555-5555` is kept as written. It is a reserved fictional number that cannot route
to anyone, and the read-back evidence in step 7 is weaker without a concrete value. The
name is the PII here; the number is not.

---

# Fixes applied — 2026-07-28, same day

## Defect 1 — hallucinated confirmation: FIXED

`BASE_INSTRUCTIONS` in `appointment_agent.py` gains a "What you may confirm" block:
confirm only what a tool recorded, name only the time when reading a booking back, and
say plainly that an unrecordable request cannot be guaranteed rather than folding it
into the confirmation. The origin bug is documented in a comment above the constant,
in the same style as the date-anchor comment below it.

**Honest limitation: this is a prompt fix, and prompt fixes are probabilistic.** It
lowers the odds of a hallucinated confirmation; it cannot make them zero the way
`extra="forbid"` makes an invented argument impossible. A deterministic fix would mean
constraining what the agent may say — out of scope for this artifact. **Not yet
re-tested on live audio.** Until it is, the correct claim is "found, understood, and
mitigated in the prompt," never "fixed."

## Defect 2 — PII in the framework's log line: FIXED and verified

New module `src/voice_agent/log_redaction.py`. A `logging.Filter` on the
`livekit.agents` logger rewrites `record.arguments` through the **same**
`sanitize_args` the runtime uses, so one definition of "sensitive" now governs both
writers to the log. Installed at import in `src/agent.py`, because every LiveKit job
runs in its own process and the framework can log a tool call before any per-session
setup would run.

Design decisions worth keeping:

- **Fails closed.** Any payload that will not parse into a mapping is replaced
  wholesale. A redactor that passes the original through whenever it is confused is
  not a redactor — the one oddly-shaped payload that breaks the parser is precisely
  the one worth not printing.
- **A filter, not `setLevel(INFO)`.** The framework's DEBUG stream carries
  `received user transcript`, `user turn committed` and `aec warmup active` — the lines
  that made runs 1-3 diagnosable at all. Silencing the stream to hide one field would
  have cost more than it saved, and would have left the guarantee depending on which
  CLI verb someone typed.
- **Censors, never drops.** The line still records which tool ran.

Verified by replaying the exact run-3 payload through the installed filter:

```
BEFORE fix : {"slot_id": "2026-07-29T13:00", "caller_name": "<real name>", "phone": "3055555555", ...}
AFTER  fix : {"slot_id": "2026-07-29T13:00", "caller_name": "J***[redacted]", "phone": "******5555", ...}
```

14 regression tests in `tests/test_log_redaction.py`, including a fail-closed
parametrized set and an end-to-end assertion through the real `livekit.agents` logger.
Suite: **68 -> 82 tests, all passing.**

## Defect 3 — the 9.9 ms artifact: DOCUMENTED, NOT FIXED

Excluded from the statistics and explained in the README. The suggested floor (~100 ms)
in `_instrument_barge_in` is **not implemented** — it would be inventing a threshold
from a single observation. Left as a known limitation.

---

# Run 4 — 2026-07-28, 15:29–15:32 EDT (fix verification)

Log: `logs/agent_run4.log` (46,932 bytes) · Recording: `~/Desktop/Screen Recording 2026-07-28 at 3.29.11 PM.mov` (277 MB)
Caller: John Doe / ten 1s. Slot booked: `2026-07-29T15:00`.

## ✅ Defect 1 — hallucinated confirmation: FIXED, verified twice

The booking confirmation named the time and nothing else:

> *"Your dental appointment is booked for tomorrow at three PM. If you have anything else, just let me know."*

Compare run 3's *"set for 1 PM tomorrow with a female doctor."* Then two unrecordable
requests, one scripted and one improvised, both handled correctly:

| Caller | Agent |
|---|---|
| *"I would like to have a female doctor."* | *"I can pass along your preference for a female doctor, **but I can't guarantee it on this call.**"* |
| *"And also maybe a wheelchair accessibility in the facility."* | *"I will make a note about your request for wheelchair accessibility, **but I cannot guarantee it on this call.**"* |

**Residual coverage gap, stated plainly.** In run 3 the preference was given **before**
the booking and got folded into the confirmation. In run 4 both requests came **after**
the booking had already been confirmed. The exact run-3 ordering was therefore not
reproduced. What is proven: the agent declines to guarantee unrecordable requests, and
its booking confirmation carries no invented detail. What is not yet proven: that a
preference stated *before* booking stays out of the confirmation. **Run 5 should state
the preference first.** Until then this is strong evidence, not a closed regression.

## ✅ Defect 2 — PII in the framework's log line: FIXED, verified in the real log

The `livekit.agents` DEBUG line, which in run 3 carried the raw name and number:

```
15:31:42,611 - DEBUG livekit.agents - executing tool {"function": "book_appointment",
  "arguments": "{\"slot_id\": \"2026-07-29T15:00\", \"caller_name\": \"J***[redacted]\",
  \"phone\": \"******1111\", \"email\": null, \"reason\": \"d***[redacted]\"}"
```

Masked, in production conditions, by the filter — not by a replay. Note `"phone":
"[redacted]"` on the invalid attempt: `_mask_phone` returns a full redaction below four
digits rather than exposing a short number. Correct.

**Known limitation this does NOT fix.** The caller's spoken words still reach the log
verbatim through `received user transcript` and `conversation_item_added`. "John Doe"
appears four times in `agent_run4.log` for exactly that reason. The redactor closes the
*structured argument* leak; free-form transcript is a different and harder problem,
because nothing marks which spoken words are PII. **Do not claim the logs are PII-free.**
The defensible claim is: tool arguments are masked at both writers.

## ✅ Step B — invalid arguments, first live execution

Never exercised on live audio before. Caller gave "one one one":

```json
{"tool": "book_appointment", "args": {..., "phone": "[redacted]"}, "status": "invalid",
 "attempts": 0, "latency_ms": 3.71, "error": "invalid arguments — phone: string_too_short"}
```

`attempts: 0` — the handler was never reached. The agent's recovery:

> *"The phone number you gave is too short to book the appointment. Could you please provide the full phone number with all digits?"*

Named the problem, asked again, invented nothing, and did not claim the booking went
through. The error surfaced to the LLM names the field and the failure type and never
echoes the caller's value.

## 🔴 NEW DEFECT 4 — the agent understated availability

The calendar was fresh, so `2026-07-29` had all four slots open: 09:00, 10:00, 13:00, 15:00.
One `check_availability` call returned them at 15:29:56. Then:

| Time | Line |
|---|---|
| 15:30:08 | agent: *"Tomorrow at four is actually not available, but I see openings at nine AM or ten AM."* |
| 15:30:12 | caller: *"So you sure you don't see anything for the afternoon?"* |
| 15:30:23 | agent: **"Yes, for tomorrow specifically, there is only a slot at three PM in the afternoon."** |

**13:00 was open.** No second `check_availability` was issued — the agent had the correct
data in context and misreported it. It did not *invent* a slot (that guard holds, five
runs running); it asserted an exclusivity the tool result contradicts.

This is defect 1's family — a claim stronger than the tool data supports — and the new
"What you may confirm" block does not cover it, because that block governs *confirmations*,
not *descriptions of availability*. A caller who needed 1 PM would have been told it did
not exist.

Suggested wording, unbuilt: extend the guard to cover any statement about what is or is
not available, not just booking read-backs.

## 🔴 NEW DEFECT 5 — the barge-in dataset is contaminated, and the headline number is wrong

Run 3 excluded one 9.9 ms sample as a coincident stop. Run 4 produced a 67.4 ms sample of
the same kind. That prompted a cross-check of **every** barge-in sample ever logged,
against whether the assistant's text was actually truncated at the interruption point —
truncation being the independent evidence that playout really was cut off.

| Log | Samples logged | Corroborated by truncation |
|---|---|---|
| `agent_measured.log` (run 2) | 4 | **1** |
| `agent_run3.log` | 4 | **1** |
| `agent_run4.log` | 9 | **2** |
| **Total** | **17** | **4** |

**Only 4 of 17 logged "barge-ins" show truncated assistant speech.** The other 13 show a
complete sentence ending in terminal punctuation — the agent finished on its own and the
caller happened to start talking around the same moment.

| | n | range | median |
|---|---|---|---|
| As previously claimed | 17 | 10–1100 ms | 751 ms |
| Truncation-corroborated only | **4** | **502–1100 ms** | **900 ms** |

**The 349 ms figure is almost certainly not a barge-in.** Both 349 ms samples (run 2 and
run 4) show complete assistant sentences. That number has been in the README and in the
claimable-evidence list since 2026-07-27, and it is the fastest figure in the set — which
is exactly why it was worth checking and why the README's existing "do not quote a
sub-500 ms figure" caution turns out to have been righter than it knew.

Run 2's writeup said the discrimination "works; it is not labelling every stop a
barge-in." That was over-confident. It catches the easy case — a stop with no user onset
at all — and misses the coincident one entirely.

**Honest limitation on this correction too.** Truncation is a heuristic, not ground truth:
a genuine barge-in landing on the last word of a sentence would look complete and be
misclassified here. So **n=4 is a lower bound on real barge-ins**, and the true figure sits
somewhere between the two rows above. What is now certain is that the n=17 row is wrong.

**A latency floor is not the fix.** It would catch 9.9 ms and 67.4 ms and nothing else —
the coincident stops at 349, 751, 802 and 904 ms are indistinguishable from real ones by
duration alone. The fix is to correlate the stop against whether playout was actually
truncated, which is the signal already sitting in the log. Unbuilt.

## Not reached, again

Steps 8 (double-fire), 9 (uninterruptible write), 10 (`capture_lead`) and 5 (the cough).
Four runs, still never executed on live audio. The cough is 0 for 4.

---

# Fixes applied — 2026-07-28, after run 4

All four verified by test; **none verified on live audio yet.** Run 5 is the gate.
Suite: **82 -> 104 tests**, `ruff check` and `ruff format --check` clean.

## Defect 4 — availability overclaim: guard extended

`BASE_INSTRUCTIONS` gains an availability clause: describe exactly the times
`check_availability` returned, never call a time the only / last / closest one unless the
result shows that, and when asked about part of a day, name every slot in that range.

Same probabilistic caveat as defect 1 — it is a prompt, not a schema. A2 and A1 of the
run-5 script test it.

## Defect 5 — barge-in measurement: stopped inferring

New `src/voice_agent/instrumentation.py`, replacing the inline `_instrument_barge_in` in
`agent.py`. Three versions of one measurement, and the module docstring keeps all three
because the failure mode repeated:

| | Method | Why it failed |
|---|---|---|
| v1 | Infer from log ordering | Measured the logger, not the agent — a constant 2–5 ms offset across three different configs |
| v2 | `user_state` onset → `agent_state` leaves speaking | Real, but *any* stop after a user onset counted. 13 of 17 were coincidences |
| v3 | Ask `SpeechHandle.interrupted` | The framework's own answer to "was playout cut off" |

Design decisions:

- **The coincident overlap is kept, not deleted.** It moves to `user_overlap_ms`. It is
  real data about how often a caller lands on the agent's last word; it was only ever
  wrong as a *latency* figure.
- **Unknown is reported as unknown.** If the handle cannot be observed,
  `playout_interrupted` is `null` and no latency is claimed. Unknown never becomes a claim.
- **Implausible overlaps are flagged, not dropped.** Under 100 ms on a genuine
  interruption sets `implausible: true`, so nobody has to rediscover why the minimum looks
  impossible.

**`agent_false_interruption` is now logged.** Step 5 — cough / non-speech rejection — was
scored "not observed" across four runs. The event fires; run 3's log carries
`resumed false interrupted speech`. Nothing was listening for it. **It was never
unobservable, only unobserved**, and four runs of manual retries could never have fixed
that. Worth remembering the next time a manual step refuses to produce evidence: check
that something is listening before concluding nothing happened.

Tests: 10 in `tests/test_instrumentation.py`. Two drive a real `AgentSession` through the
offline harness to prove `SpeechHandle.interrupted` is trustworthy under LiveKit's own
control path; the rest map that signal to log records. Recorded there and here: offline,
`agent_state_changed` never reaches "speaking" — the agent state machine needs room IO —
so the event path cannot be integration-tested without a room. That is a limitation of the
test, stated rather than hidden.

## Transcript PII — content dropped, shape kept

Masking tool arguments closed the structured leak. Run 4 still logged "John Doe" four
times through `received user transcript` and `conversation_item_added`, on a call whose
arguments were fully masked.

`TranscriptRedactor` replaces spoken content with its word count — `[9 words redacted]` —
and leaves every timing field intact, so turn and latency analysis still work on a redacted
log. Assistant text is redacted too, because the agent echoes caller PII when it reads a
number back. This is only safe because barge-in detection no longer depends on reading
assistant text out of the log; under the old instrumentation it would have broken the
measurement.

`LOG_TRANSCRIPTS=1` opts back in for an acceptance run, and the session start line states
which mode is active so a log can never be mistaken for the other one.

**Honest scope.** This is content-dropping, not PII detection. Nothing here identifies
*which* words were sensitive — it declines to log any of them. That is the right default
for a shipped deployment and a nuisance during acceptance work, which is what the env var
is for. A real NER-based redactor would be a different and much larger artifact, and would
bring its own false-negative problem.

---

# Run 5 — 2026-07-28, 15:58–16:02 EDT (fix verification)

Log: `logs/agent_run5a.log` · Recording: `~/Desktop/Screen Recording 2026-07-28 at 3.57.53 PM.mov` (353 MB)
`LOG_TRANSCRIPTS=1`. Session B was **not run** — see "Outstanding".

## ✅ Defect 1 — closed, with the ordering run 4 missed

The caller stated the preference **first**, in the opening request: *"I want to book a
dental appointment. With a female doctor on Friday, ten thirty AM."* That is the exact
shape of the run-3 bug, and it held at both ends:

| | |
|---|---|
| First response | *"…please note **I can't guarantee a specific doctor**, but I will pass along your preference for a female doctor."* |
| Booking confirmation | *"Your dental appointment is booked for 1:00 PM on Friday."* — time only |

Run 3 said *"set for 1 PM tomorrow with a female doctor."* Run 5 did not, with the
preference in context from the first sentence. **Defect 1 is closed.**

## ✅ Defect 4 — closed

> *"On Friday, we have openings at **9:00, 10:00, 13:00, and 15:00**. We don't have 10:30 available."*

All four slots named. Run 4's failure was *"there is only a slot at three PM in the
afternoon"* with 13:00 open. Held twice more under pressure: *"We have 13:00 available, but
not 13:30"* and *"We have 10:00 and 13:00, but not 12:00"* — naming alternatives without
asserting exclusivity. **Defect 4 is closed.**

## 🔴 Defect 5 — the fix was WRONG in the opposite direction

17 stops: **2 interrupted, 15 not.**

| Time | interrupted | barge_in_latency_ms | user_overlap_ms |
|---|---|---|---|
| 15:59:05 | **true** | **699.1** | — |
| 15:58:57 | false | null | 1299.2 |
| 15:59:15 | false | null | 999.0 |
| 15:59:31 | false | null | 948.5 |
| 15:59:42 | false | null | 3594.7 |
| 15:59:58 | false | null | 1098.7 |
| 16:01:29 | false | null | 1374.5 |
| 16:01:46 | false | null | 950.6 |
| 16:02:07 | false | null | 373.0 |
| 16:02:23 | true | null | — |

**Eight stops carried an overlap that the old instrumentation would have reported as a
barge-in.** Every one now sits in `user_overlap_ms` with a null latency. The ratio matches
the 13-of-17 historical cross-check, from an independent run.

The 16:02:23 row is worth reading: interrupted, but no user onset preceded it — the session
closed mid-word (*"You're welcome. Take"*). Interrupted with no latency claimed is the
correct output, and under v2 it would have been silently dropped instead.

### …and then Jean said "barge-in doesn't seem to work as before"

He was right, and the log proves it. Cross-checking the same run against the independent
tell — whether the assistant's own text was cut off mid-sentence:

| | count |
|---|---|
| Assistant utterances truncated mid-sentence | **6** |
| Reported by v3 as interrupted | **2** |

**Four real barge-ins were reported as coincidental stops.** v2 over-counted; v3
under-counted. Both looked right until real-call data was checked against something
outside the instrument.

**Root cause.** v3 read `session.current_speech` when the agent began speaking and asked
that handle whether it was interrupted. `current_speech` is not reliably the utterance
that just ended: preemptive generation — which this log shows on nearly every turn —
queues the next speech while the current one is still playing, and the agent state does
not dip to "listening" between two queued utterances. So the captured handle can belong
to a different utterance by the time the stop arrives, and a fresh handle reports
`interrupted == False`.

**v4, applied.** Use the per-utterance record instead of a mutable session pointer.
`agent_activity.py` builds each assistant `ChatMessage` with
`interrupted=speech_handle.interrupted`, taken from the exact handle that produced it, and
emits `conversation_item_added` **synchronously, immediately before** flipping the state to
"listening" (`agent_activity.py:2836-2839`). The item that arrives just before a stop is
therefore the utterance that is ending. The handle survives as a fallback for stops with no
preceding item; the item wins whenever present.

Four new regression tests, including one that reproduces the v3 bug directly: a stale
handle saying "not interrupted" must lose to an item saying it was.

**The transferable lesson.** Three of four versions of this measurement were wrong, and
each was discovered the same way — by checking the instrument against a signal from
outside it. A measurement that only agrees with itself is not evidence. **Do not quote a
barge-in figure from this project until it has been cross-checked against a run's
transcripts.**

**No latency figure survives run 5.** n=1 under v3, and v3 is now known to be broken. v4
has not run live at all. There is currently no defensible barge-in number, which remains
the correct state.

## 🔴 NEW DEFECT 6 — the read-back did not match the write, and no explicit yes was given

The instruction is unambiguous: *"Before booking, read the caller's phone number back to
them digit by digit and get an explicit yes. A wrong number makes the booking worthless."*

| Time | |
|---|---|
| 16:01:03 | agent: *"…area code 5, 5, 5 then 5, 5, 5, then 5, 5, 5, 5 — is that right? **And your full name is still John?**"* |
| 16:01:06 | caller: *"My name is Jean Doe."* |
| 16:01:08 | `book_appointment` fires |

Two failures in three lines:

1. **The digits written do not match the digits read back.** The read-back is ten digits.
   The logged mask is `*******5555` — **eleven**. (The earlier rejected attempt masks to
   `****5555`, eight, matching the agent's own *"that's eight digits"*, so the mask
   arithmetic is sound.)
2. **No explicit yes was obtained.** The agent asked two questions in one breath — number
   *and* name — the caller answered only the name, and the agent treated that as
   confirmation of both and wrote immediately.

This is worse than defects 1 and 4, because it is a failure *of the safety mechanism
itself*. The read-back exists to catch a wrong number; here it neither matched the write
nor waited for agreement. A booking with an eleven-digit callback number is exactly the
"worthless booking" the instruction names.

Candidate fixes, unbuilt:

- **Never bundle the confirmation question with another question.** One question, one
  answer, then write.
- **Echo the digit count and require it to match** what the tool receives — cheap, and it
  would have caught the 10-vs-11 discrepancy deterministically rather than by prompt.
- The second is the stronger fix: it does not depend on the model behaving.

## ⚠️ Cough test — now observable, still unresolved

`agent_false_interruption` is instrumented and **zero events were logged.** That is now a
real answer rather than a blind spot: either no non-speech noise was attempted this run, or
one was and it did not trip. Five runs in, the honest score remains "not observed" — but
for the first time the instrument is listening.

## Outstanding

| Item | Status |
|---|---|
| **Session B — transcript redaction proof** | **Not run.** The feature is unverified live. |
| Step 8 — double-fire | Never run, five runs |
| Step 9 — uninterruptible write | Never run, five runs |
| Step 10 — `capture_lead` | Never run, five runs |
| Barge-in figure | n=1 under the new instrumentation |


---

# Run 5, session 2 — 2026-07-28, 16:12–16:14 EDT (v4 live)

Same log (`agent_run5a.log`, room `console-f3c14640`), same process, second console session.
Recording: `~/Desktop/Screen Recording 2026-07-28 at 4.12.12 PM.mov` (213 MB).

**It did run v4.** `instrumentation.py` was written at 16:07:54; the job process (pid 80928)
initialised at 16:12:15; LiveKit uses `spawn` on macOS, so the child re-imported from disk.

## Defects 1 and 4 held again

- *"We have openings on Friday at 9 AM, 10 AM, 1 PM, and 3 PM."* — all four named.
- *"Twelve thirty is not an option we have available on Friday."* — refused to invent, sixth run.
- *"Your dental appointment is booked for 10 AM on Friday."* — time only.
- *"I can't guarantee a female doctor or wheelchair accessibility right now, but I will pass
  your request along."* — both requests, one sentence, no promise.
- Unprompted generalisation: asked to arrange **food**, it answered *"Food can't be arranged
  through the appointment, but I will let the team know."* The guard is holding on a category
  nobody wrote it for.

## Defect 6 did NOT recur — and that is not the same as fixed

> *"To confirm, your phone number is 5 5 5 5 5 5 5 5 5 5, correct?"* — *"Yeah. That's right."*

One question, explicit yes, then the write. Logged mask `******5555` = **10 digits**, matching
the read-back exactly. **No fix has been applied.** Run 5 session 1 bundled two questions and
wrote 11 digits against a 10-digit read-back; session 2 did neither. That is an intermittent
defect, which is harder to trust than a consistent one, and it stays open.

## Defect 5 — v4 was better and still wrong

| | session 1 (v3) | session 2 (v4) |
|---|---|---|
| Utterances truncated mid-sentence | 6 | 2 |
| Reported interrupted | 2 | 1 |

The two session-2 cases show the cause exactly:

```
16:12:42  assistant item (truncated)  ->  stop: interrupted=True     correct
16:12:25  stop: interrupted=False     ->  assistant item (truncated) MISSED
```

v4 assumed the item always arrives before the stop. It does on the generation path
(`agent_activity.py:2836-2839`) and not on the `generate_reply` path used for the greeting.
When the stop came first there was nothing to read, so v4 fell back to the stale handle and
inherited v3's bug.

### v5 — applied

Stop assuming an order. The stop carries the *timing*, the item carries the *verdict*; each
is stashed as half a record and emitted once both are in hand, whichever lands first. A stop
still unpaired when the next utterance begins is emitted as `unpaired: true` rather than
dropped — silent loss is the failure this module exists to stop repeating.

**And the cross-check now ships inside the record.** `text_truncated` — did the assistant's
own text stop mid-sentence — is the independent tell that caught v2, v3 and v4, every time by
hand and after the fact. When it disagrees with `playout_interrupted`, the record carries
`signals_disagree: true`:

```json
{"event": "agent_stopped_speaking", "playout_interrupted": false,
 "barge_in_latency_ms": null, "user_overlap_ms": 0.0,
 "text_truncated": true, "signals_disagree": true}
```

Every earlier version of this measurement would have been caught in one run by that field.

**Five versions, three wrong.** v1 measured the logger. v2 counted coincidences. v4 counted
one path. Each looked correct until checked against something outside itself. The honest
summary is not "v5 is right" — it is that **v5 is the first version that reports when it
might be wrong.** Still zero live-verified samples.

## Still never run, six runs in

Steps 8 (double-fire), 9 (uninterruptible write), 10 (`capture_lead`), the cough, and
session B (the transcript-redaction proof — both sessions ran with `LOG_TRANSCRIPTS=1`).

---

# Run 5, session 3 — 2026-07-28, 16:35–16:37 EDT (v5 live, first time)

Room `console-79927579`. Recording: `~/Desktop/Screen Recording 2026-07-28 at 4.34.45 PM.mov` (172 MB).

## v5 worked — and immediately found something none of v1–v4 could have

9 stops. **Zero truncated utterances** — every assistant line ends in terminal punctuation.
Two stops carry `playout_interrupted: true`. So the two signals disagree twice, and the
record says so:

| # | text | int | trunc | barge_ms | flag |
|---|---|---|---|---|---|
| 2 | *"…Would you like to pick any of these?"* | true | false | 399.7 | `signals_disagree` |
| 3 | *"…you want the appointment at ten AM on Thursday, July 30, 2026?"* | true | false | — | `signals_disagree` |

In both, the caller answered **as the agent landed its final word** — *"Alright. That's"*
and *"Yeah."* The framework issued an interrupt; the agent had already said everything it
meant to say.

**Neither signal is wrong. They answer different questions.**

- `SpeechHandle.interrupted` = an interrupt was *issued*.
- `text_truncated` = words were actually *lost*.

### The real finding: "barge-in" was never defined

Five versions of this instrument argued about *mechanism*. None of them established what
the thing being measured **is**. Three defensible definitions, and they give different
numbers from the same call:

| Definition | Session 3 samples |
|---|---|
| (a) An interrupt was issued | **2** (399.7 ms, and one with no measurable onset) |
| (b) Speech was actually cut off | **0** |
| (c) Both agree | **0** |

Under (a) this call produced a 399.7 ms figure. Under (b) or (c) it produced none. That
gap is not noise — a caller answering on the agent's last syllable is a different event
from a caller cutting it off mid-word, and only one of them is what "the agent yields fast
when you talk over it" claims.

**This is why v5 was worth building even though it produced no number.** v1–v4 would each
have silently picked a side. This one asked.

**Recommendation: definition (c) for anything quoted, (a) retained in the log.** A figure
you can defend to a client is one where two independent signals agree. Keep the issued-
interrupt count as a separate, honestly-named statistic.

## Everything else held

- **Defect 4**, fourth run: *"We don't have eleven AM available on Thursday, but we have
  slots at nine AM, ten AM, one PM, and three PM."*
- **Defect 1**, fourth run: *"I can't guarantee those details on this call, but I will pass
  your request along."* Booking confirmation named the time only.
- **Date anchoring**, seventh run: "Thursday" → 2026-07-30.
- **Defect 6 did not recur.** Read-back *"five five five, five five five, five five five,
  five five"* = 11 digits; logged mask `*******5555` = 11 digits; explicit *"Yep."* before
  the write. **2 of 3 sessions correct, 1 wrong. Intermittent, unfixed, still open.**

## Still never run — seven calls in

Steps 8 (double-fire), 9 (uninterruptible write), 10 (`capture_lead`); the cough test
(five runs, zero `agent_false_interruption` events); and session B, the transcript-redaction
proof — every session so far has run with `LOG_TRANSCRIPTS=1`.

**No deliberate mid-word barge-in has been performed since v5 shipped**, so definition (b)
and (c) still have zero samples by construction rather than by measurement.

---

# Run 7 — 2026-07-28, 16:52–16:58 EDT (the gate)

Logs: `agent_run7a.log` (session A), `agent_run7b.log` (session B).
Recording on the Desktop. Definition (c) live: a barge-in counts only when the framework
issued an interrupt **and** the agent's words were actually cut off.

## ✅ THE BARGE-IN FIGURE — first defensible dataset

| # | ms |
|---|---|
| 1 | 449.2 |
| 2 | 549.3 |
| 3 | 652.8 |
| 4 | 1199.5 |
| 5 | 1200.2 |

**n=5 · min 449 · median 653 · max 1200 · mean 810.** Every sample has
`playout_interrupted: true` **and** `text_truncated: true`.

A sixth stop (16:53:44) was a confirmed interruption with no measurable user onset, so it
reports no latency rather than an invented one. One stop (16:53:09) carries
`signals_disagree` — an interrupt issued on a sentence that completed — and is correctly
excluded. Under the old instrumentation that would have entered the dataset as a 344 ms
barge-in, and it would have been the second-fastest number on record.

**The distribution is not tight.** Three samples cluster 449–653 ms and two sit at
~1200 ms. n=5, one caller, one mic, one region. Quote it as *"the agent yields inside
about a second, typically around 650 ms"* and nothing finer.

## ✅ Cough / false-positive rejection — RESOLVED after six runs

```
16:55:43  {"event": "agent_false_interruption", "resumed": true}
```

Six runs scored this "not observed." The event fired the moment something was listening
for it. **It was never unobservable, only unobserved** — and no amount of coughing harder
would have changed that. The instrument was the problem, not the acoustics.

## ✅ `capture_lead` — first live execution, eight runs in

`16:57:00 capture_lead ok att=1 phone=******0142`, after a read-back the caller confirmed.
Covered by unit tests since day one; now covered by a call.

## ✅ Transcript redaction — PROVEN, fourth time of asking

Session B startup: `transcript logging: redacted`. Zero occurrences of the caller's name.
Every transcript line reduced to a word count:

```
"user_transcript": "[4 words redacted]"    "text": "[15 words redacted]"
```

No digit run over six characters appears anywhere in the file. This is the check that
separates a redaction feature from a redaction claim, and it passes.

## ✅ Defects 1, 4 and 6 all held

- **4:** *"Tomorrow afternoon, we have two times at 1 PM and 3 PM."*
- **1:** *"I can book for 1 PM tomorrow, but I can't guarantee a female doctor. I'll pass
  your preference along."* Confirmation named the time and the name, nothing invented.
- **6:** two read-backs, two matches. `3 0 5 5 5 5 5 5 5 5` read back → 10 digits written;
  `3 0 5 5 5 5 0 1 4 2` read back → 10 digits written. The structural check never had to
  refuse, because nothing tried to write a mismatch.
- **Invalid phone:** nine digits → `value_error` → *"the phone number you gave doesn't have
  enough digits for our system."* No bluff, no invented digits.

## 🔴 NEW — `capture_lead` writes a phone number with no read-back check

`book_appointment` refuses to write unless the agent's own read-back matches. `capture_lead`
takes `caller_name` and `phone` and has **no such check.** It happened to read back
correctly in run 7; nothing enforces it.

A wrong number in a lead is less costly than a wrong number on a booking, but the argument
for the check does not actually depend on which table the row lands in. The asymmetry is an
oversight, not a decision. **Unfixed.**

## 🔴 NEW — the agent recites full phone numbers in closing statements

> *"All set for 1 PM tomorrow for John Doe, phone number 3 0 5 5 5 5 5 5 5 5."*
> *"Someone will call you back at 3 0 5 5 5 5 0 1 4 2 soon."*

Read-back before a write is deliberate and valuable. Reciting the number again *after* the
write is neither, and on a real call it is spoken aloud into whatever room the caller is
standing in. A one-line instruction change. **Unfixed.**

## ⚠️ Steps 8 and 9 — still not exercised, for an interesting reason

Asked to book the same slot twice, the agent **did not attempt the duplicate write.** It
called `check_availability`, saw 1 PM was gone, and offered the remaining slots:

> *"The 1 PM slot tomorrow is now booked, but 9 AM, 10 AM, and 3 PM are still available."*

That is better behaviour than the test expected — and it means the `once_per_turn` ledger
and the calendar's conflict guard have **still never fired on a live call.** Both remain
unit-tested only. Step 9 (a barge-in during a write) likewise never occurred, because no
write was in flight when the caller talked over it.

Forcing these would mean scripting the model into a mistake it is now avoiding. **Recorded
as untested rather than chased.**

## ⚠️ The deliberate defect-6 probe was not performed

The script asked for a confirmation answered with a *different* question, to test whether
the agent writes without agreement. The caller confirmed properly both times. The structural
check verifies the digits match; **nothing yet verifies the caller said yes.** Open.

---

# Run 8 — 2026-07-28, 22:24–22:27 EDT (first call on hosted infrastructure)

**The first call in this project's history not answered by a laptop.**

| | |
|---|---|
| Platform | Azure Container Apps, `eastus2` |
| Revision | `ca-voice-agent--i2w0xyg` |
| Image | `voice-agent:9327aad` · `sha256:b70a3df35440ee77b7c61a70aa28cb7a77e7a5fa4500c85cd4e138f5005fc619` |
| Worker | `AW_Kzyc635VEpXg`, LiveKit region `US East B`, protocol 17 |
| Job | `AJ_Trkg3JdeCLZi`, room `console-a6abce5c` |
| Client | Browser Agent Console from the operator's Mac |
| Caller | Synthetic — Marcus Webb, 305-555-0142, `DemoCalendar` |

**Control that makes this meaningful:** no local `dev` process was running. A single worker
was registered against the LiveKit project, so the job could only have gone to the container.

> **Log preservation note — read this before trusting the numbers below.**
> The resource group was deleted before the log was exported. The attempted dump, run two
> minutes later, returned:
>
> ```
> ERROR: Not Found({"error":{"code":"ResourceNotFound","message":"The Resource
> 'Microsoft.App/containerApps/ca-voice-agent' under resource group 'rg-voice-agent'
> was not found."}})
> ```
>
> `logs/azure_run8.log` is therefore a **terminal capture**, not an `az` export, and it says
> so in its own header. The Log Analytics workspace went with the resource group.
>
> What that does and does not undermine: every figure in this section was **re-derived
> programmatically from that file** — the JSON events were parsed, not eyeballed — so the
> counts and latencies are exactly what the agent emitted. What is lost is the ability to go
> back for anything not already captured. That is a real cost, and it was avoidable.

---

## ✅ Cold start — 2.46 seconds, with the model bake skipped

```
02:16:11.689  starting worker            version 1.6.7, rtc-version 1.1.13
02:16:11.689  plugin registered          livekit.plugins.deepgram
02:16:11.689  plugin registered          livekit.plugins.openai
02:16:11.689  plugin registered          livekit.plugins.silero
02:16:11.705  HTTP server listening on :8081
02:16:14.152  registered worker          agent_name=appointment-agent  region="US East B"
```

Three claims from `infra/README.md` are settled by those six lines:

1. **Silero loaded from the wheel.** The image ships an empty `HF_HOME` — the 461 MB
   turn-detector bake was skipped via `BAKE_TURN_DETECTOR=0` — and the VAD still came up.
   The offline test predicted this; the container confirmed it.
2. **The turn detector is genuinely never loaded.** Only three plugins registered. The
   `livekit-plugins-turn-detector` package is installed and was never touched, which is
   exactly why baking its weights would have been 461 MB of dead image.
3. **Port 8081, not a random dev port.** `HTTP server listening on :8081` — so the liveness
   and startup probes in `infra/containerapp.yaml` point at something real.

`starting worker` → `registered worker` = **2.46 s**.

---

## ✅ THE HEADLINE — the read-back guard fired on a live call and blocked a wrong write

```
02:26:27.921  ToolError while executing tool: the number you read back does not match
              the number you are booking — read it back again digit by digit and get
              an explicit yes
              function=book_appointment  speech_id=speech_660da7e4ec1e  level=WARNING
```

Twenty-two seconds later, after the agent re-read the digits and the caller confirmed:

```
02:26:49.522  "tool": "book_appointment",
              "turn_id": "speech_a6c4b3be0d3e",
              "idempotency_key": "tk_3aa1ca609cd54d792d65c97c6e2ab129",
              "args": {"slot_id": "2026-07-30T09:00",
                       "caller_name": "M***[redacted]",
                       "phone": "******0142",
                       "email": null,
                       "reason": "d***[redacted]"},
              "status": "ok", "attempts": 1, "latency_ms": 0.23
```

**This is the most valuable line the project has produced.** `verify_readback` was written on
2026-07-28 in response to run 5's defect 6, where the agent read back eleven 5s and wrote ten.
The first version of that check compared *trailing digits* and would have let the original bug
through — every suffix matched — which is why it ships as exact equality instead.

Here it caught a genuine mismatch, on a hosted worker, on live audio, and forced a correction
before anything was written. Note the write that followed shows `"attempts": 1` — the guard
raised before the handler ran, so nothing partial was committed and no retry was consumed.

A passing unit test proves a guard is *wired*. This proves it *works on the thing it was
built for*. Claimable without qualification.

---

## ✅ PII redaction held on hosted infrastructure

```
02:24:50.195  transcript logging: redacted
```

and in the only tool call carrying caller data:

```
"caller_name": "M***[redacted]",  "phone": "******0142",  "reason": "d***[redacted]"
```

Every prior verification of redaction was on a laptop, where the operator controls the log
file. This one ran into **Azure Log Analytics** — a third-party sink, retained by default,
readable by anyone with subscription access. Not one word of caller speech reached it.

`LOG_TRANSCRIPTS` is deliberately absent from `infra/containerapp.yaml`; this is the run that
shows why that mattered.

---

## ✅ Barge-in instrument v5 — validated on infrastructure it was not developed on

Twelve agent stops in one call. The v5 record classifies each by pairing the stop with its own
assistant item and requiring both signals to agree:

| Class | Count | What the record says |
|---|---|---|
| **Confirmed barge-in** | **4** | `playout_interrupted: true`, `text_truncated: true`, latency reported |
| **Coincident stop** | **4** | both `false`, `barge_in_latency_ms: null`, `user_overlap_ms` set |
| **Clean finish** | **4** | both `false`, both timings `null` |

The four confirmed samples:

| Time | ms |
|---|---|
| 02:25:31.53 | **801.3** |
| 02:25:41.38 | **551.9** |
| 02:26:01.98 | **400.7** |
| 02:26:24.68 | **702.4** |

**n=4 · min 400.7 · median 627.15 · max 801.3 · mean 614.1**
*(computed by parsing `logs/azure_run8.log`, not by reading the table above.)*

And the four the instrument correctly refused to count, with their overlaps:
**533.7 · 253.0 · 229.8 · 18.0 ms.**

> **That 18.0 ms entry is the whole argument for v5 in one number.** Under the v2 instrument —
> "any stop following a user onset" — this call would have reported **eight** barge-ins instead
> of four, and the fastest would have been **18 milliseconds**. That figure would have been
> quoted in a proposal. It describes the agent finishing its sentence at the same moment the
> caller started talking, which is not an interruption at all.
>
> The contamination rate here is **exactly 50%**, on hosted infrastructure, in a call the
> instrument was not tuned against. The 13-of-17 figure from run 5 was not a fluke of one
> laptop's acoustics.

### Combined dataset

| Run | Platform | Samples (ms) |
|---|---|---|
| 7 | local (macOS) | 449 · 549 · 653 · 1200 · 1200 |
| 8 | Azure Container Apps | 400.7 · 551.9 · 702.4 · 801.3 |

**n=9 · min 400.7 · median 653 · max 1200 · mean 723.**

**The "never quote sub-450 ms" rule is superseded.** 400.7 ms is corroborated by both signals
and is now the floor. The honest sentence becomes: *the agent yields inside about a second,
typically around 650 ms, with a measured floor near 400 ms.*

**Do not read run 8's tighter spread as evidence that Azure is faster.** The audio topology
changed — browser → LiveKit `US East B` → worker in `eastus2`, versus browser → LiveKit →
worker on the same Mac. Two different paths, n=4 on one of them. It is a wider dataset, not
a comparison.

---

## ✅ Everything else that held

| Behaviour | Evidence |
|---|---|
| Config reached the container intact | `credentials_present: [all 5]`, `credentials_missing: []` |
| Shipping barge-in config unchanged in prod | `interruption_min_duration: 0.4`, `min_words: 2`, `endpointing: [0.4, 3.0]` |
| `check_availability` executes | ×2, `status: ok`, `attempts: 1`, 0.14 ms and 0.15 ms |
| Idempotency keys derived per turn | `tk_ee2cf80…` and `tk_232170d…` — different turns, different keys |
| Teardown | The three `data channel closed unexpectedly` ERRORs at 02:27:52 are the known, expected shutdown noise. Not a defect. |

The two `check_availability` calls carry **different `turn_id`s** (`speech_df6c7c652729`,
`speech_b7aca4305cc8`), so this is the agent re-checking across turns, not a replay. It does
not exercise the `once_per_turn` ledger.

---

## ⚠️ Still not exercised, nine calls in

Unchanged by this run, and listed so the tally stays honest:

1. **`once_per_turn` ledger** — never fired live.
2. **Calendar conflict guard** — never fired live.
3. **Barge-in during an uninterruptible write** — no write was in flight when the caller
   talked over the agent.
4. **`capture_lead` read-back check** — the defect logged in run 7 is still unfixed, and
   `capture_lead` was not called in this run.
5. **Nothing verifies the caller said yes.** The structural check confirms the digits match;
   agreement is still unverified. Run 8 does not change this — the caller confirmed properly.

## ✅ Teardown — verified clean, 2026-07-28 23:32 EDT

`az group delete -n rg-voice-agent --yes --no-wait` was issued at ~22:30 EDT. Confirmed complete
about an hour later:

```
az group list -o table                 -> (empty)
az resource list ... -o table          -> (empty)
az group delete -n ME_cae-voice-agent_rg-voice-agent_eastus2 --yes
                                       -> ResourceGroupNotFound
```

**Nothing is billing.** `az group list` returning empty means the subscription is back to its
pre-deploy state — `rg-voice-agent` was the only group in it. The `ME_` check matters because
Azure Container Apps can create a managed infrastructure resource group *outside* the one you
delete; here it either never existed or cleaned itself up. Checked rather than assumed.

The Log Analytics workspace (`workspace-rgvoiceagentZ0p7`) lived inside `rg-voice-agent` and went
with it — which is also why the run-8 logs were unrecoverable from Azure.

**Cost posture:** this deployment is stood up for a demonstration and torn down after. The durable
asset is `infra/`, which reproduces the whole environment in about ten minutes. `minReplicas: 1`
with no ingress means an always-on replica billed per vCPU-second rather than per call, and
Microsoft's free grant (180,000 vCPU-seconds / 360,000 GiB-seconds per month) covers roughly 7% of
one 1-vCPU / 2-GiB replica running continuously. Leaving it up is a recurring charge for nothing.

## 🔴 Process defect — the log was not preserved before teardown

The resource group was deleted with `--no-wait` before `logs/azure_run8.log` was written.
The reconstruction from the terminal capture is complete for everything quoted here, but
anything not already extracted is unrecoverable, and so is the Log Analytics workspace.

**Rule this creates:** *write the log to disk before deleting the resource group.* Added to
`infra/README.md`'s verification section. The teardown command is cheap to re-run; the
evidence is not.
