from datetime import datetime, timezone

from app.models import Message, MessageStatus
from app.send.pacing import (
    budget_remaining, effective_daily_cap, is_within_window, sent_today,
)

IST = "Asia/Kolkata"


def _utc(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


def test_within_window_weekday_inside():
    # 2026-08-12 is a Wednesday. 06:00 UTC = 11:30 IST → inside 09:30–18:30.
    assert is_within_window(_utc(2026, 8, 12, 6, 0),
                            start_hhmm="09:30", end_hhmm="18:30", tz=IST)


def test_outside_window_before_start():
    # 03:00 UTC = 08:30 IST → before 09:30.
    assert not is_within_window(_utc(2026, 8, 12, 3, 0),
                                start_hhmm="09:30", end_hhmm="18:30", tz=IST)


def test_outside_window_weekend():
    # 2026-08-15 is a Saturday.
    assert not is_within_window(_utc(2026, 8, 15, 6, 0),
                                start_hhmm="09:30", end_hhmm="18:30", tz=IST)


def test_effective_cap_ramp_then_steady():
    first = _utc(2026, 8, 12, 6, 0)
    within = _utc(2026, 8, 15, 6, 0)   # 3 days later → still ramp
    after = _utc(2026, 8, 20, 6, 0)    # 8 days later → steady
    assert effective_daily_cap(within, first, daily_cap=30, ramp_cap=15,
                               ramp_days=7, tz=IST) == 15
    assert effective_daily_cap(after, first, daily_cap=30, ramp_cap=15,
                               ramp_days=7, tz=IST) == 30


def test_effective_cap_no_first_send():
    assert effective_daily_cap(_utc(2026, 8, 12, 6, 0), None, daily_cap=30,
                               ramp_cap=15, ramp_days=7, tz=IST) == 15


def test_sent_today_counts_only_today(session):
    now = _utc(2026, 8, 12, 6, 0)
    session.add(Message(draft_id=1, status=MessageStatus.SENT,
                        sent_at=_utc(2026, 8, 12, 5, 0)))   # today IST
    session.add(Message(draft_id=2, status=MessageStatus.SENT,
                        sent_at=_utc(2026, 8, 11, 5, 0)))   # yesterday
    session.add(Message(draft_id=3, status=MessageStatus.QUEUED,
                        sent_at=_utc(2026, 8, 12, 5, 30)))  # not SENT
    session.commit()
    assert sent_today(session, now, tz=IST) == 1


def test_sent_today_treats_stored_naive_ts_as_utc(session):
    # SQLite round-trips sent_at as tz-naive. 2026-08-12 20:00 UTC is
    # 2026-08-13 01:30 IST — it must count as IST "today" 2026-08-13, not be
    # mis-read as host-local wall time. (Strict guard on non-UTC hosts.)
    now = _utc(2026, 8, 13, 5, 0)  # 10:30 IST on 2026-08-13
    session.add(Message(draft_id=1, status=MessageStatus.SENT,
                        sent_at=_utc(2026, 8, 12, 20, 0)))
    session.commit()
    assert sent_today(session, now, tz=IST) == 1


def test_effective_cap_naive_first_send_at_treated_as_utc():
    # first_send_at read back from SQLite is naive. 2026-08-06 20:00 UTC is
    # 2026-08-07 IST; with now on 2026-08-13 IST, elapsed is 6 days → still
    # ramp. Mis-reading the naive value as host-local gives 7 → steady.
    first_naive = datetime(2026, 8, 6, 20, 0)  # naive, as read back from the DB
    now = _utc(2026, 8, 12, 20, 0)             # 2026-08-13 01:30 IST
    assert effective_daily_cap(now, first_naive, daily_cap=30, ramp_cap=15,
                               ramp_days=7, tz=IST) == 15


def test_budget_remaining_floors_at_zero(session):
    now = _utc(2026, 8, 12, 6, 0)
    for i in range(3):
        session.add(Message(draft_id=i + 1, status=MessageStatus.SENT,
                            sent_at=_utc(2026, 8, 12, 5, 0)))
    session.commit()
    # ramp cap 2, already 3 sent → 0, not negative
    assert budget_remaining(session, now, None, daily_cap=30, ramp_cap=2,
                            ramp_days=7, tz=IST) == 0
