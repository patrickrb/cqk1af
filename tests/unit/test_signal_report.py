from __future__ import annotations

from cqk1af.nlp.signal_report import parse_signal_report


def test_five_nine_words() -> None:
    assert parse_signal_report("you are five nine") == "59"


def test_five_by_nine() -> None:
    assert parse_signal_report("five by nine") == "59"


def test_fifty_nine() -> None:
    assert parse_signal_report("you're fifty nine") == "59"


def test_digits_59() -> None:
    assert parse_signal_report("5 9 here") == "59"


def test_three_digit_rst() -> None:
    assert parse_signal_report("five nine nine") == "599"


def test_low_report() -> None:
    assert parse_signal_report("you are three by four") == "34"


def test_none_for_no_report() -> None:
    assert parse_signal_report("nice to meet you") is None


def test_invalid_rst_returns_none() -> None:
    # First digit must be 1-5; "7" is impossible
    assert parse_signal_report("seven by nine") is None
