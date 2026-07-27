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
