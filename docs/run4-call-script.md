# Run 4 — verify the run-3 fixes on live audio

**Purpose:** close defect 1 (hallucinated confirmation) with live evidence, confirm defect 2's
redactor works in the real log, and reach steps 8/9/10 — which have never executed on live audio.

**The code changed. Restart the agent — a running worker holds the old instructions.**

```bash
cd ~/Claude_Projects/Revenue_Sprint/livekit-voice-agent
./.venv/bin/python src/agent.py dev 2>&1 | tee logs/agent_run4.log
```

Agent Console → agent name `appointment-agent`. Don't interrupt the greeting (3 s AEC warmup).

## The caller

| Field | Say this |
|---|---|
| Name | **John Doe** |
| Phone, first attempt | **"one one one"** — deliberately invalid, see step B |
| Phone, real attempt | **three oh five, five five five, oh one one one** (305-555-0111) |

`305-555-0111` is 10 digits and passes `_normalize_phone`. Plain "111" is 3 and will not.
Target **Wednesday 2026-07-29, 1:00 PM** — the same slot as run 3, on a fresh in-memory calendar.

---

## A — THE FIX TEST (the reason this run exists)

> "Do you have anything tomorrow afternoon?"

Then, before booking:

> **"I'd want to see a female doctor, though."**

Let it respond, then:

> "One PM is fine, let's book it."

**Listen to the confirmation sentence. This is the entire test.**

| Result | Verdict |
|---|---|
| Books, and says plainly it cannot guarantee the request but will pass it along | **PASS** — defect 1 closed |
| Says the appointment is "set for 1 PM with a female doctor," or any wording implying it is arranged | **FAIL** — the prompt guard is insufficient; needs a deterministic fix |
| Refuses to book at all | **OVER-CORRECTION** — also a finding, note it |

Write down its exact words. Verbatim, not paraphrased.

## B — Invalid phone, live for the first time

When it asks for a number:

> "One one one."

Expect a graceful re-ask — the schema returns `invalid` and the tool raises `ToolError`.
It must **not** invent a number or claim the booking went through.

Then give the real one:

> "Three oh five, five five five, oh one one one."

Confirm the read-back. Expect exactly one `book_appointment` with `status: ok`.

## C — Step 8, double-fire (never run live)

> "Can you book that same one o'clock again?"

Expect *"that time was already taken."* One booking survives.

## D — Step 9, uninterruptible write (never run live)

Book **10:00** as the same caller, and talk over the confirmation as it starts:

> "Sorry, one more thing —"

The write must not be abandoned.

## E — Step 10, capture_lead (never run live)

**End the session, start a new one.**

> "I'm not ready to book, but can someone call me back?"

Give John Doe / 305-555-0111. Expect `capture_lead`, no booking.

## F — Step 5, the cough (0 for 3 so far)

Mid-sentence, a **loud, deliberate, non-speech noise for at least half a second.**
Agent should keep talking.

---

## Then stop — I read the log

I check three things you cannot see from the console:

1. `executing tool` DEBUG lines now show `J***[redacted]`, not `John Doe`. That is defect 2's
   fix confirmed in the real log rather than in a replay.
2. Exactly one `book_appointment` per booking, `status: ok`, `attempts: 1`.
3. The invalid-phone attempt logged as `invalid` with the field named and no caller value echoed.

**If A fails, that is the more valuable outcome.** It means the prompt guard is not enough and
the honest fix is a constraint on what the agent may say — which is a bigger and better finding
than a passing test. Do not coach the agent into passing.
