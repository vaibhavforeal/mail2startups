from datetime import datetime, timezone

from app.inbox.service import _sweep_no_response
from app.models import (
    Draft, DraftMode, DraftStatus, Message, MessageStatus, MessageType,
    Startup, StartupStatus,
)


def _utc(y, mo, d):
    return datetime(y, mo, d, 12, 0, tzinfo=timezone.utc)


def _startup(session, name, *, initial_at, followup_at=None,
             followup_draft_status=None):
    s = Startup(name=name, domain=name + ".io", source="yc",
                status=StartupStatus.SENT)
    session.add(s); session.commit()
    init = Draft(startup_id=s.id, type=MessageType.INITIAL, mode=DraftMode.FORMAL,
                 subject="Hi", body="x", status=DraftStatus.APPROVED)
    session.add(init); session.commit()
    session.add(Message(draft_id=init.id, type=MessageType.INITIAL,
                        sent_at=initial_at, smtp_message_id="<i>",
                        status=MessageStatus.SENT))
    session.commit()
    if followup_at is not None:
        fu = Draft(startup_id=s.id, type=MessageType.FOLLOWUP,
                   mode=DraftMode.CASUAL, subject="Re: Hi", body="bump",
                   status=DraftStatus.APPROVED)
        session.add(fu); session.commit()
        session.add(Message(draft_id=fu.id, type=MessageType.FOLLOWUP,
                            sent_at=followup_at, smtp_message_id="<f>",
                            status=MessageStatus.SENT))
        session.commit()
    if followup_draft_status is not None:
        fu = Draft(startup_id=s.id, type=MessageType.FOLLOWUP,
                   mode=DraftMode.CASUAL, subject="Re: Hi", body="bump",
                   status=followup_draft_status)
        session.add(fu); session.commit()
    return s


def test_sweep_clock_measures_from_followup(session):
    s = _startup(session, "A", initial_at=_utc(2026, 8, 1),
                 followup_at=_utc(2026, 8, 6))
    # Aug 15 is 14 days after the initial, but only 9 after the follow-up → keep
    assert _sweep_no_response(session, _utc(2026, 8, 15), 14, mutate=True) == 0
    assert s.status == StartupStatus.SENT
    # Aug 20 is 14 days after the follow-up → give up
    assert _sweep_no_response(session, _utc(2026, 8, 20), 14, mutate=True) == 1
    assert s.status == StartupStatus.NO_RESPONSE


def test_sweep_skips_pending_followup(session):
    s = _startup(session, "B", initial_at=_utc(2026, 8, 1),
                 followup_draft_status=DraftStatus.PENDING_REVIEW)
    assert _sweep_no_response(session, _utc(2026, 8, 20), 14, mutate=True) == 0
    assert s.status == StartupStatus.SENT


def test_sweep_gives_up_on_rejected_followup(session):
    s = _startup(session, "C", initial_at=_utc(2026, 8, 1),
                 followup_draft_status=DraftStatus.REJECTED)
    assert _sweep_no_response(session, _utc(2026, 8, 20), 14, mutate=True) == 1
    assert s.status == StartupStatus.NO_RESPONSE
