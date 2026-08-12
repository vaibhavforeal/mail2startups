from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Draft, DraftStatus, Event, Startup, StartupStatus,
)


@dataclass
class SendResult:
    draft_id: int
    sent: bool
    reason: str | None = None


def approve_drafts(session: Session, ids: list[int] | None = None, *,
                   all_pending: bool = False) -> int:
    """Approve drafts by id (or every pending_review draft). Moves each
    Draft→approved and its Startup→queued. Returns the count approved."""
    query = select(Draft).where(Draft.status == DraftStatus.PENDING_REVIEW)
    if not all_pending:
        query = query.where(Draft.id.in_(ids or []))
    drafts = session.scalars(query).all()
    for draft in drafts:
        draft.status = DraftStatus.APPROVED
        startup = session.get(Startup, draft.startup_id)
        if startup is not None:
            startup.status = StartupStatus.QUEUED
        session.add(Event(startup_id=draft.startup_id, kind="approved",
                          payload={"draft_id": draft.id}))
    session.commit()
    return len(drafts)


def reject_drafts(session: Session, ids: list[int]) -> int:
    """Reject drafts by id. Moves each Draft→rejected and its Startup→dead."""
    drafts = session.scalars(
        select(Draft).where(Draft.id.in_(ids),
                            Draft.status == DraftStatus.PENDING_REVIEW)).all()
    for draft in drafts:
        draft.status = DraftStatus.REJECTED
        startup = session.get(Startup, draft.startup_id)
        if startup is not None:
            startup.status = StartupStatus.DEAD
        session.add(Event(startup_id=draft.startup_id, kind="rejected",
                          payload={"draft_id": draft.id}))
    session.commit()
    return len(drafts)
