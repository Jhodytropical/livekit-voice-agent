# Run 5 — verify all four fixes, and close the paths four runs have missed

**Two sessions, one process.** Session A logs transcripts so the call can be analysed.
Session B runs with the shipping default to prove redaction is actually live.

**Restart required** — `src/agent.py` changed.

## Session A — the acceptance call

```bash
cd ~/Claude_Projects/Revenue_Sprint/livekit-voice-agent
LOG_TRANSCRIPTS=1 ./.venv/bin/python src/agent.py dev 2>&1 | tee logs/agent_run5a.log
```

Look for `transcript logging: ENABLED — this log will contain caller speech` in the
startup line. That log stays on your machine; `logs/` is gitignored.

Caller: **John Doe**, **305-555-0111**. Don't interrupt the greeting.

### A1 — defect 4: does it name every afternoon slot?

The calendar is fresh, so tomorrow has 09:00, 10:00, 13:00 and 15:00 open.

> "What have you got tomorrow afternoon?"

| Result | Verdict |
|---|---|
| Names **both 1 PM and 3 PM** | **PASS** — defect 4 closed |
| Says 3 PM is the only afternoon slot, or names just one without saying there are others | **FAIL** — the guard does not reach availability either |

Run 4's failing line, for comparison: *"there is only a slot at three PM in the afternoon."*

### A2 — defect 1, the ordering run 4 missed

State the preference **before** booking. This is the exact shape of the run-3 bug.

> "Before we book — I'd need a female doctor. Can you do one PM?"

Then let it take your name and number and confirm.

**Listen to the confirmation.** It must name the time and must not say the appointment
is arranged *with* a female doctor. It should say plainly it can't guarantee that.

### A3 — step 8, double-fire (never run live)

> "Can you book that same one o'clock again?"

Expect *"that time was already taken."* One booking survives.

### A4 — step 9, uninterruptible write (never run live)

Book **3 PM** as the same caller, and cut in the moment it starts confirming:

> "Sorry, one more thing —"

The write must not be abandoned.

### A5 — step 5, the cough (0 for 4, now instrumented)

Mid-sentence, a **loud non-speech noise, at least half a second.** The agent should keep
talking. This has never reached the log — not because it did not happen, but because
nothing was listening. `agent_false_interruption` is now logged, so this run settles it
either way.

### A6 — a deliberate real barge-in

Ask something open — *"tell me how the appointment works"* — and cut in **mid-word** with
*"actually hold on."* I need at least one stop where `playout_interrupted: true`, to
confirm the new instrumentation still catches genuine interruptions and has not simply
stopped reporting them.

### A7 — step 10, capture_lead (never run live)

End the session, start a new one.

> "I'm not ready to book, but can someone call me back?"

Give the same name and number. Expect `capture_lead`, no booking.

---

## Session B — prove redaction is live

Stop the process. Restart **without** the env var:

```bash
./.venv/bin/python src/agent.py dev 2>&1 | tee logs/agent_run5b.log
```

Startup should say `transcript logging: redacted`. Then a 20-second call is enough:

> "Hi, my name is John Doe, what have you got tomorrow?"

End it. That's all session B is for.

---

## What I check afterward

| # | Check | Where |
|---|---|---|
| 1 | Both 1 PM and 3 PM named for the afternoon | run5a transcript |
| 2 | Confirmation carries no unrecorded preference, with the preference stated first | run5a transcript |
| 3 | `playout_interrupted: true` on the A6 barge-in, `false` on every natural finish | both logs |
| 4 | No `barge_in_latency_ms` on any completed utterance | both logs |
| 5 | `agent_false_interruption` present, or provably absent | run5a |
| 6 | Exactly one `book_appointment` per booking; double-fire refused | run5a |
| 7 | `capture_lead` fires with no booking | run5a |
| 8 | run5b shows `[N words redacted]` and **no** "John Doe" anywhere | run5b |

Check 8 is the one that matters most: it is the difference between a redaction feature
and a redaction claim.

**A failure anywhere is still the better outcome than a coached pass.** Four runs have
each produced a finding that changed the code. Do not steer the agent into passing.
