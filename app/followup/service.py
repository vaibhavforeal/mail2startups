from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import anthropic
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.followup.claude_followup import (
    FollowupPlan, MalformedFollowupError, followup_plan,
)
from app.models import (
    Draft, DraftMode, DraftStatus, Event, Message, MessageStatus, MessageType,
    Startup, StartupStatus,
)


@dataclass
class FollowupResult:
    startup_id: int
    drafted: bool


def _as_utc(dt: datetime) -> datetime:
    """SQLite round-trips DateTime(timezone=True) as tz-naive; pin it to UTC
    before comparing to an aware now (we always store UTC)."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _initial_draft(session, startup_id):
    return session.scalars(
        select(Draft)
        .where(Draft.startup_id == startup_id, Draft.type == MessageType.INITIAL)
        .order_by(Draft.id).limit(1)).first()


def _initial_sent_at(session, startup_id):
    return session.scalar(
        select(func.max(Message.sent_at))
        .join(Draft, Message.draft_id == Draft.id)
        .where(Draft.startup_id == startup_id,
               Message.type == MessageType.INITIAL,
               Message.status == MessageStatus.SENT))


def _has_followup(session, startup_id) -> bool:
    has_draft = session.scalar(
        select(Draft.id).where(Draft.startup_id == startup_id,
                               Draft.type == MessageType.FOLLOWUP)) is not None
    has_msg = session.scalar(
        select(Message.id).join(Draft, Message.draft_id == Draft.id)
        .where(Draft.startup_id == startup_id,
               Message.type == MessageType.FOLLOWUP)) is not None
    return has_draft or has_msg


def _log_failed(session, startup_id, reason, **extra):
    session.add(Event(startup_id=startup_id, kind="followup_failed",
                      payload={"reason": reason, **extra}))
    session.commit()


def draft_followups(session: Session, *, resume, now, settings,
                    generator=followup_plan, limit: int = 50,
                    dry_run: bool = False) -> list[FollowupResult]:
    cutoff = now - timedelta(days=settings.followup_delay_days)
    results: list[FollowupResult] = []
    made = 0
    for s in session.scalars(
            select(Startup).where(Startup.status == StartupStatus.SENT)
            .order_by(Startup.id)).all():
        if made >= limit:
            break
        if _has_followup(session, s.id):
            continue
        sent_at = _initial_sent_at(session, s.id)
        if sent_at is None or _as_utc(sent_at) > cutoff:
            continue
        initial = _initial_draft(session, s.id)
        if initial is None:
            continue
        sid = s.id
        try:
            plan: FollowupPlan = generator(s, resume, initial.subject, initial.body)
        except (MalformedFollowupError, anthropic.AnthropicError, ValueError) as exc:
            session.rollback()
            if not dry_run:
                _log_failed(session, sid, "provider_error", detail=str(exc))
            results.append(FollowupResult(sid, False))
            continue
        if not dry_run:
            subj = initial.subject if initial.subject.lower().startswith("re:") \
                else "Re: " + initial.subject
            session.add(Draft(
                startup_id=sid, contact_id=initial.contact_id,
                type=MessageType.FOLLOWUP, mode=DraftMode.CASUAL,
                subject=subj, body=plan.body, resume_pdf_path=None,
                status=DraftStatus.PENDING_REVIEW))
            session.add(Event(startup_id=sid, kind="followup_drafted", payload={}))
            session.commit()
        results.append(FollowupResult(sid, True))
        made += 1
    return results
