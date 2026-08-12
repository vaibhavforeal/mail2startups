from datetime import datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Message, MessageStatus


def _parse_hhmm(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


def is_within_window(now: datetime, *, start_hhmm: str, end_hhmm: str, tz: str) -> bool:
    """True on Mon–Fri when the tz-local clock time is within [start, end]."""
    local = now.astimezone(ZoneInfo(tz))
    if local.weekday() >= 5:  # Sat=5, Sun=6
        return False
    return _parse_hhmm(start_hhmm) <= local.time() <= _parse_hhmm(end_hhmm)


def effective_daily_cap(now: datetime, first_send_at: datetime | None, *,
                        daily_cap: int, ramp_cap: int, ramp_days: int, tz: str) -> int:
    """ramp_cap during the ramp window (or before any send), else daily_cap."""
    if first_send_at is None:
        return ramp_cap
    elapsed = (now.astimezone(ZoneInfo(tz)).date()
               - first_send_at.astimezone(ZoneInfo(tz)).date()).days
    return ramp_cap if elapsed < ramp_days else daily_cap


def sent_today(session: Session, now: datetime, *, tz: str) -> int:
    """Count SENT messages whose sent_at falls on today's tz-local date."""
    today = now.astimezone(ZoneInfo(tz)).date()
    rows = session.scalars(
        select(Message.sent_at).where(Message.status == MessageStatus.SENT)).all()
    return sum(1 for ts in rows
               if ts is not None and ts.astimezone(ZoneInfo(tz)).date() == today)


def budget_remaining(session: Session, now: datetime, first_send_at: datetime | None, *,
                     daily_cap: int, ramp_cap: int, ramp_days: int, tz: str) -> int:
    cap = effective_daily_cap(now, first_send_at, daily_cap=daily_cap,
                              ramp_cap=ramp_cap, ramp_days=ramp_days, tz=tz)
    return max(0, cap - sent_today(session, now, tz=tz))
