"""Structurally enforce the phone-number read-back before a booking is written.

The instruction has always said it: *"read the caller's phone number back to them
digit by digit and get an explicit yes. A wrong number makes the booking worthless."*
Run 5 showed an instruction is not a guarantee. In session 1 the agent bundled two
questions — *"…is that right? And your full name is still John?"* — the caller answered
only the name, and the agent wrote immediately. **The read-back was ten digits. The write
was eleven.** Sessions 2 and 3 did it correctly. An intermittent failure in the mechanism
that exists to catch wrong numbers is worse than a consistent one, because it passes
review.

Defects 1 and 4 were fixed with wording, and that is appropriate for *what the agent
says*. This one is about what the agent *writes*, so it gets a check that does not depend
on the model behaving: the digits in the agent's own most recent read-back must match the
digits reaching the tool, or the write is refused.

Speech-to-text renders numbers both ways — session 1 produced ``"5, 5, 5, 5"`` and
session 3 produced ``"five five five, five five five"`` — so both forms are parsed.
"""

from __future__ import annotations

import re

_WORD_DIGITS = {
    "zero": "0",
    "oh": "0",
    "o": "0",
    "nought": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
}

#: How far back to look for a read-back. The agent may say "one moment" or ask a
#: follow-up between reading the number and writing, so the immediately preceding
#: message is not always the right one.
LOOKBACK_MESSAGES = 4

_TOKEN = re.compile(r"[a-z]+|\d")


def extract_digits(text: str) -> str:
    """Pull a digit string out of spoken or written text.

    ``"5, 5, 5"`` and ``"five five five"`` both yield ``"555"``. Words that are not
    digits are skipped rather than terminating the scan, so ``"area code five five
    five, then five five five"`` reads as one number — which is how callers say them.
    """
    if not isinstance(text, str):
        return ""
    out: list[str] = []
    for token in _TOKEN.findall(text.lower()):
        if token.isdigit():
            out.append(token)
        elif token in _WORD_DIGITS:
            out.append(_WORD_DIGITS[token])
    return "".join(out)


def find_readback(messages: list[str], *, minimum: int = 7) -> str | None:
    """The most recent assistant message containing a plausible phone read-back.

    Args:
        messages: Assistant messages, most recent last.
        minimum: Digits required before a message counts as a read-back. Guards against
            reading a date or a time as a phone number.
    """
    for text in reversed(messages[-LOOKBACK_MESSAGES:]):
        digits = extract_digits(text)
        if len(digits) >= minimum:
            return digits
    return None


class ReadbackMismatch(Exception):
    """Raised when the write does not match what the caller was told.

    Carries a message written for the model, naming what to do rather than what went
    wrong: the agent's next move should be to read the number back again, not to
    apologise or retry the same arguments.
    """


def verify(phone: str, assistant_messages: list[str]) -> None:
    """Check the number about to be written against the agent's own read-back.

    Raises:
        ReadbackMismatch: if no read-back was found, or it disagrees with ``phone``.
    """
    written = extract_digits(phone)
    # A number too short to be a phone number is the schema's problem, not this
    # check's. Letting it through here means the caller hears "that number is too
    # short" instead of "read it back again", which is the more useful correction.
    if len(written) < 7:
        return

    spoken = find_readback(assistant_messages)

    if spoken is None:
        raise ReadbackMismatch(
            "read the phone number back to the caller digit by digit and get an "
            "explicit yes before booking"
        )
    # Exact equality, deliberately. The first version compared trailing digits to
    # tolerate a spoken country code — and it let the real bug through, because run 5's
    # number was eleven 5s read back as ten 5s, and every suffix matched. The defect was
    # the *count*, so the count is what has to match.
    #
    # Known cost: a read-back message that also contains a date ("…0142, and your
    # appointment is July 30") extracts extra digits and is refused. That is the safe
    # direction, and the refusal tells the agent to read the number back on its own —
    # which is what its instructions asked for in the first place.
    if spoken != written:
        raise ReadbackMismatch(
            "the number you read back does not match the number you are booking — read "
            "it back again digit by digit and get an explicit yes"
        )
