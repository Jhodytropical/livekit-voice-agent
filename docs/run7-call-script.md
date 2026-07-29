# Run 7 — the last call before the push

Four things, in order. **Restart first** — `agent.py`, `appointment_agent.py`,
`instrumentation.py` and the new `readback.py` all changed.

Caller: **John Doe**, **305-555-0142**. Read the number as *"three oh five, five five
five, oh one four two."*

---

## Session A — everything except the redaction proof

```bash
cd ~/Claude_Projects/Revenue_Sprint/livekit-voice-agent
LOG_TRANSCRIPTS=1 ./.venv/bin/python src/agent.py dev 2>&1 | tee logs/agent_run7a.log
```

### A1 — defect 6, now structural

Book anything. When it asks for your number, give it **once, clearly**.

The agent must read it back and get a yes before writing. If it tries to write without a
matching read-back, `book_appointment` now **refuses** and tells it to read the number
back — that refusal in the log is a pass, not a failure.

Then, deliberately: when it reads the number back, **answer a different question instead**
— say *"Yes, and my name is John Doe"* without confirming the digits. Watch whether it
still books. (It may; the structural check verifies the digits match, not that you agreed.
Note what happens either way — that is the remaining gap.)

### A2 — three deliberate mid-word barge-ins

**This is the one that has never been done properly.** Ask something open —
*"tell me how the appointment works"* — and cut in **while it is mid-word**, not at the
end of a sentence:

> "actually hold on"

**Do it three times, at different points in different sentences.** Every barge-in so far
has landed on a final word, which is why the strict definition still has zero samples.
Cutting it off mid-sentence is the whole measurement.

### A3 — step 8, double-fire

> "Can you book that same time again?"

Expect *"that time was already taken."*

### A4 — step 9, uninterruptible write

Book a second slot and talk over the confirmation as it starts:

> "Sorry, one more thing —"

The booking must survive.

### A5 — step 5, the cough

Mid-sentence, a **loud non-speech noise for at least half a second.** Five runs, zero
`agent_false_interruption` events logged. Now instrumented, so this settles it.

### A6 — step 10, capture_lead

End the session, start a new one:

> "I'm not ready to book, but can someone call me back?"

Give the name and number. Expect `capture_lead`, no booking.

---

## Session B — the redaction proof

Stop the process. Restart **without** the env var:

```bash
./.venv/bin/python src/agent.py dev 2>&1 | tee logs/agent_run7b.log
```

Startup must say `transcript logging: redacted`. Then twenty seconds:

> "Hi, my name is John Doe, what have you got tomorrow?"

End it. **Skipped three times now — this is the only check that separates a redaction
feature from a redaction claim.**

---

## What I check

| # | Check |
|---|---|
| 1 | ≥3 records with `text_truncated: true` AND `playout_interrupted: true` — the first quotable barge-in samples |
| 2 | Any `signals_disagree` records, and which direction |
| 3 | `book_appointment` refused when the read-back does not match |
| 4 | Double-fire refused; one booking survives |
| 5 | `capture_lead` fires with no booking |
| 6 | `agent_false_interruption` present or provably absent |
| 7 | run7b: `[N words redacted]` and **no** "John Doe" anywhere |

Check 1 is the gate. Without three clean mid-word cut-ins there is still no barge-in
figure, and the README will keep saying so.
