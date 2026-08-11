from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import EnrichmentUsage


def current_period(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m")


def _row(session: Session, provider: str, period: str) -> EnrichmentUsage | None:
    return session.scalars(
        select(EnrichmentUsage).where(
            EnrichmentUsage.provider == provider,
            EnrichmentUsage.period == period,
        )
    ).first()


def usage_this_month(session: Session, provider: str, period: str | None = None) -> int:
    period = period or current_period()
    row = _row(session, provider, period)
    return row.calls if row else 0


def record_call(session: Session, provider: str, period: str | None = None) -> int:
    period = period or current_period()
    row = _row(session, provider, period)
    if row is None:
        row = EnrichmentUsage(provider=provider, period=period, calls=0)
        session.add(row)
    row.calls += 1
    session.commit()
    return row.calls


def remaining(session: Session, provider: str, limit: int, period: str | None = None) -> int:
    return max(0, limit - usage_this_month(session, provider, period=period))
