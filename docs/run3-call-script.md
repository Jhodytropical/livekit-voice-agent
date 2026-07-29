# Run 3 — acceptance call script (steps 7–12)

**Date:** 2026-07-28 · **Target:** the paths `book_appointment` has never reached.
**Mode:** `dev` + Agent Console in the browser (same as runs 1 and 2).

Runs 1 and 2 both died at step 7 for the same reason — the caller declined to give a
phone number. This script exists to make sure that does not happen a third time.
**Say the synthetic details out loud when asked. They are fake by design.**

---

## The synthetic caller — memorize these three lines

| Field | Say this |
|---|---|
| Name | **Marcus Webb** |
| Phone | **three oh five, five five five, oh one four two** (305-555-0142) |
| Email | *only if asked* — **marcus dot webb at example dot com** |

`555-01xx` is the reserved fictional-number range and `example.com` is RFC 2606
reserved. Neither can reach a real person. The calendar is `DemoCalendar` —
in-memory, nothing is written anywhere. Nothing about you enters this call.

**Target slot: Wednesday 2026-07-29 at 9:00 AM.** Tomorrow, a weekday, and run 2
already confirmed the agent returns real slots for that date.

---

## 1. Start it

```bash
cd ~/Claude_Projects/Revenue_Sprint/livekit-voice-agent
./.venv/bin/python src/agent.py dev 2>&1 | tee logs/agent_run3.log
```

Wait for `registered worker`. Then open the **Agent Console** from the LiveKit Cloud
dashboard, set **Agent name** to `appointment-agent`, and start a session.

> The `tee` matters — runs 1 and 2 were reconstructed from redirected logs. Without it
> there is no evidence afterward and the call has to be re-shot.

**Do not interrupt the greeting.** Interruptions are suppressed for the first 3.00 s
(`aec warmup active`). Barging in there records a false failure.

---

## 2. The script — say these in order

### Step 3 — warm-up (already passing, gets you to the booking)

> "Hi, what have you got open on Wednesday the twenty-ninth?"

Expect spoken times only from **09:00 / 10:00 / 13:00 / 15:00**. Any other time is a
regression — stop and note it.

### Step 7 — THE BOOKING (this is the whole point of run 3)

> "Nine in the morning works. Let's book it."

The agent will ask for a name and number. **Give them. Do not deflect this time.**

> "Marcus Webb. Three oh five, five five five, oh one four two."

Then let it read the number back. Confirm:

> "That's correct."

Watch the tool pane. **Expect exactly one `book_appointment`.**
Note whether it read the digits back individually — that is a graded step.

### Step 8 — double-fire

> "Actually, can you book that same nine o'clock again?"

Expect a refusal — *"that time was already taken"*. One booking must remain.

> Note: this exercises the **calendar** conflict guard, not the idempotency ledger.
> A new turn gets a new `turn_id`, so it is not a replay. The ledger guards a
> same-turn double-fire, which the model rarely produces on its own. Both guards
> being separate is deliberate — do not report this step as proof of the ledger.

### Step 9 — talk over the confirmation

Book a **second, different** slot (10:00) with the same caller, and this time start
talking the instant the agent begins confirming:

> "Ten AM instead — same name and number."

…then as it confirms, cut in with:

> "Sorry, one more thing —"

**The write must not be abandoned.** The booking should still exist.

### Step 5 — the cough (open from both prior runs)

While the agent is mid-sentence, make a **loud, deliberate, non-speech noise for at
least half a second**. Not a polite throat-clear — both previous attempts produced
nothing in the log at all. The agent should keep talking (`min_words=2` holding).

### Step 10 — lead capture, fresh session

**End the session and start a new one.** Then:

> "I'm not ready to book yet, but can someone follow up with me?"

Give the same synthetic name and number when asked.
Expect a **`capture_lead`** call — no booking.

### Step 12 — close

End the session. The three `data channel closed unexpectedly` ERROR lines at teardown
are expected and are not a defect.

---

## 3. What "done" looks like

`book_appointment` appears in `logs/agent_run3.log` with `"status": "ok"` and
`"attempts": 1`, exactly once for the 9:00 slot. That single line is what makes the
bio claim true.

**If it fails, that is still a result.** Runs 1 and 2 each produced a finding that
changed the code. Do not smooth over a failure to make the step pass.
