from sqlalchemy import select

from app.models import (
    Contact, Draft, DraftMode, DraftStatus, Startup, StartupStatus,
)
from app.send.service import approve_drafts, reject_drafts


def _drafted(session, name, domain, *, mode=DraftMode.FORMAL):
    s = Startup(name=name, domain=domain, source="yc", status=StartupStatus.DRAFTED)
    session.add(s)
    session.commit()
    c = Contact(startup_id=s.id, name="C", role="CTO", email=f"c@{domain}",
                found_via="scraped", confidence=0.9, verified=True)
    session.add(c)
    session.commit()
    d = Draft(startup_id=s.id, contact_id=c.id, mode=mode, subject="Hi", body="Hello",
              resume_pdf_path=None,  # None so build_email reads no file in send tests
              status=DraftStatus.PENDING_REVIEW)
    session.add(d)
    session.commit()
    return s, d


def test_approve_moves_draft_and_startup(session):
    s, d = _drafted(session, "A", "a.io")
    assert approve_drafts(session, [d.id]) == 1
    assert d.status == DraftStatus.APPROVED
    assert s.status == StartupStatus.QUEUED


def test_approve_all_pending(session):
    _drafted(session, "A", "a.io")
    _drafted(session, "B", "b.io")
    assert approve_drafts(session, all_pending=True) == 2
    assert len(session.scalars(
        select(Draft).where(Draft.status == DraftStatus.APPROVED)).all()) == 2


def test_reject_sets_startup_dead(session):
    s, d = _drafted(session, "A", "a.io")
    assert reject_drafts(session, [d.id]) == 1
    assert d.status == DraftStatus.REJECTED
    assert s.status == StartupStatus.DEAD
