from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cqk1af.safety.id_timer import IdTimer


def test_due_initially() -> None:
    t = IdTimer(interval_seconds=600)
    assert t.due() is True


def test_not_due_after_mark() -> None:
    t = IdTimer(interval_seconds=600)
    t.mark_id_sent()
    assert t.due() is False
    assert t.time_until_due() > 590


def test_due_after_interval() -> None:
    t = IdTimer(interval_seconds=600)
    past = datetime.now(timezone.utc) - timedelta(seconds=601)
    t.mark_id_sent(when=past)
    assert t.due() is True


def test_time_until_due_with_simulated_clock() -> None:
    t = IdTimer(interval_seconds=600)
    base = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)
    t.mark_id_sent(when=base)
    later = base + timedelta(seconds=200)
    assert 399 <= t.time_until_due(now=later) <= 400
