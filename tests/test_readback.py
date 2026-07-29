"""The write must match what the caller was told.

Regression tests for run 5, defect 6: the agent read back ten digits and wrote eleven,
having bundled the confirmation with a second question so the caller never agreed to the
number at all. Sessions 2 and 3 did it correctly — an intermittent failure in the
mechanism that exists to catch wrong numbers, which is worse than a consistent one.
"""

from __future__ import annotations

import pytest

from voice_agent.readback import ReadbackMismatch, extract_digits, find_readback, verify

# Verbatim from the logs: STT renders numbers both ways.
SPOKEN_WORDS = (
    "Let me confirm your phone number back to you: five five five, five five five, "
    "five five five, five five? Is that correct?"
)
SPOKEN_DIGITS = "To confirm, your phone number is 5 5 5 5 5 5 5 5 5 5, correct?"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("five five five, five five five, five five five, five", "55555555555"[:10]),
        ("5 5 5 5 5 5 5 5 5 5", "5555555555"),
        ("three oh five, five five five, oh one four two", "3055550142"),
        ("area code 305 then 555 0142", "3055550142"),
        ("Is that correct?", ""),
        (None, ""),
    ],
)
def test_digits_are_parsed_from_speech_or_text(text: object, expected: str) -> None:
    assert extract_digits(text) == expected  # type: ignore[arg-type]


def test_a_matching_readback_permits_the_write() -> None:
    verify("3055550142", ["Great.", "To confirm, three oh five, five five five, oh one four two?"])


def test_the_run5_session1_failure_is_now_refused() -> None:
    """Ten digits read back, eleven written. This is the bug, verbatim."""
    readback = "Let me confirm: area code 5, 5, 5 then 5, 5, 5, then 5, 5, 5, 5 — is that right?"

    with pytest.raises(ReadbackMismatch):
        verify("55555555555", [readback])  # eleven


def test_a_write_with_no_readback_at_all_is_refused() -> None:
    """The read-back is not optional. Silence must not read as agreement."""
    with pytest.raises(ReadbackMismatch, match="read the phone number back"):
        verify("3055550142", ["Great, can I have your full name for the booking?"])


def test_both_spoken_forms_are_accepted() -> None:
    verify("5555555555", [SPOKEN_DIGITS])
    verify("55555555555", [SPOKEN_WORDS])


def test_a_stale_readback_beyond_the_lookback_does_not_count() -> None:
    old = "To confirm, three oh five, five five five, oh one four two?"
    filler = ["And your name?", "Thank you.", "One moment.", "What time suits you?"]

    with pytest.raises(ReadbackMismatch):
        verify("3055550142", [old, *filler])


def test_a_date_is_not_mistaken_for_a_phone_number() -> None:
    """`minimum` exists so "ten AM on Thursday, July 30, 2026" is not a read-back."""
    assert find_readback(["Your appointment is set for ten AM on Thursday, July 30, 2026."]) is None


def test_formatting_differences_do_not_fail_a_correct_readback() -> None:
    verify("3055550142", ["To confirm, your number is 305-555-0142, correct?"])


def test_a_repeated_digit_number_of_the_wrong_length_is_caught() -> None:
    """Why the check is exact equality and not a suffix match.

    Run 5's number was eleven 5s read back as ten. Every suffix matched, so a
    trailing-digit comparison passed it — the first version of this check did exactly
    that and let the bug through. The defect was the count, so the count must match.
    """
    with pytest.raises(ReadbackMismatch):
        verify("55555555555", ["To confirm: 5 5 5 5 5 5 5 5 5 5, correct?"])


def test_a_readback_mixed_with_other_numbers_is_refused() -> None:
    """A documented cost of exact matching, asserted so it stays deliberate.

    The refusal tells the agent to read the number back on its own, which is what the
    instructions asked for anyway.
    """
    with pytest.raises(ReadbackMismatch):
        verify("3055550142", ["Your number is 305-555-0142 and your slot is July 30, 2026?"])


def test_a_number_too_short_to_be_a_phone_is_left_to_the_schema() -> None:
    """Its error names the field and the reason; this check would only say "read it
    back again", which is the less useful correction."""
    verify("111", ["Great, and your name?"])
