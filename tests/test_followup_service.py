import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from app.draft.resume_schema import load_resume
from app.followup.claude_followup import FollowupPlan, MalformedFollowupError
from app.followup.service import FollowupResult, draft_followups
from app.models import (
    Contact, Draft, DraftMode, DraftStatus, Event, Message, MessageStatus,
    MessageType, Startup, StartupStatus,
)

FIXTURE = Path(__file__).parent / "fixtures" / "resume_min.yaml"
NOW = datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc)


def _resume():
    return load_resume(FIXTURE)


def _settings(**over):
    base = dict(followup_delay_days=5)
    base.update(over)
    return types.SimpleNamespace(**base)


def _gen(startup, resume, subject, body):
    return FollowupPlan(body="Just bumping this up.")


def _sent_startup(session, name, domain, *, sent_at, status=StartupStatus.SENT):
    s = Startup(name=name, domain=domain, source="yc", status=status)
    session.add(s); session.commit()
    c = Contact(startup_id=s.id, name="C", role="CTO", email=f"c@{domain}",
                found_via="scraped", confidence=0.9, verified=True)
    session.add(c); session.commit()
    d = Draft(startup_id=s.id, contact_id=c.id, type=MessageType.INITIAL,
              mode=DraftMode.FORMAL, subject="Intern application", body="Hello",
              status=DraftStatus.APPROVED)
    session.add(d); session.commit()
    session.add(Message(draft_id=d.id, type=MessageType.INITIAL, sent_at=sent_at,
                        smtp_message_id="<init@m>", status=MessageStatus.SENT))
    session.commit()
    return s, c, d


def test_followup_drafts_silent_startup(session):
    s, c, d = _sent_startup(session, "A", "a.io", sent_at=NOW - timedelta(days=6))
    results = draft_followups(session, resume=_resume(), now=NOW,
                              settings=_settings(), generator=_gen)
    assert results == [FollowupResult(s.id, True)]
    fu = session.scalars(
        select(Draft).where(Draft.type == MessageType.FOLLOWUP)).one()
    assert fu.subject == "Re: Intern application"
    assert fu.mode == DraftMode.CASUAL and fu.resume_pdf_path is None
    assert fu.status == DraftStatus.PENDING_REVIEW
    assert fu.contact_id == c.id
    assert fu.body == "Just bumping this up."


def test_followup_skips_too_recent(session):
    _sent_startup(session, "B", "b.io", sent_at=NOW - timedelta(days=3))
    results = draft_followups(session, resume=_resume(), now=NOW,
                              settings=_settings(), generator=_gen)
    assert results == []
    assert session.scalars(
        select(Draft).where(Draft.type == MessageType.FOLLOWUP)).all() == []


def test_followup_idempotent(session):
    _sent_startup(session, "C", "c.io", sent_at=NOW - timedelta(days=6))
    draft_followups(session, resume=_resume(), now=NOW, settings=_settings(),
                    generator=_gen)
    second = draft_followups(session, resume=_resume(), now=NOW,
                             settings=_settings(), generator=_gen)
    assert second == []
    assert len(session.scalars(
        select(Draft).where(Draft.type == MessageType.FOLLOWUP)).all()) == 1


def test_followup_skips_non_sent_status(session):
    _sent_startup(session, "R", "r.io", sent_at=NOW - timedelta(days=6),
                  status=StartupStatus.REPLIED)
    results = draft_followups(session, resume=_resume(), now=NOW,
                              settings=_settings(), generator=_gen)
    assert results == []


def test_followup_provider_error_contained(session):
    s, c, d = _sent_startup(session, "E", "e.io", sent_at=NOW - timedelta(days=6))

    def boom(startup, resume, subject, body):
        raise MalformedFollowupError("bad")

    results = draft_followups(session, resume=_resume(), now=NOW,
                              settings=_settings(), generator=boom)
    assert results == [FollowupResult(s.id, False)]
    assert session.scalars(
        select(Draft).where(Draft.type == MessageType.FOLLOWUP)).all() == []
    assert session.scalars(
        select(Event).where(Event.kind == "followup_failed")).all()


def test_followup_dry_run_persists_nothing(session):
    s, c, d = _sent_startup(session, "D", "d.io", sent_at=NOW - timedelta(days=6))
    results = draft_followups(session, resume=_resume(), now=NOW,
                              settings=_settings(), generator=_gen, dry_run=True)
    assert results == [FollowupResult(s.id, True)]
    assert session.scalars(
        select(Draft).where(Draft.type == MessageType.FOLLOWUP)).all() == []
