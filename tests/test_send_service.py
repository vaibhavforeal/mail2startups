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


import types
from datetime import datetime, timezone

from app.models import Event, Message, MessageStatus
from app.send import state as state_mod
from app.send.service import SendResult, send_batch, send_test_emails

NOW = datetime(2026, 8, 12, 6, 0, tzinfo=timezone.utc)  # Wed 11:30 IST


def _settings(**over):
    base = dict(from_email="me@d.com", from_name="Me", smtp_user="me@d.com",
                test_recipient="me@d.com", send_start="00:00", send_end="23:59",
                send_timezone="Asia/Kolkata", daily_cap=30, ramp_daily_cap=15,
                ramp_days=7)
    base.update(over)
    return types.SimpleNamespace(**base)


class _Recorder:
    def __init__(self, fail=False):
        self.fail = fail
        self.sent = []

    def send(self, msg):
        if self.fail:
            import smtplib
            raise smtplib.SMTPException("boom")
        self.sent.append(msg)
        return msg["Message-ID"]


def _queued(session, name, domain, *, mode=DraftMode.FORMAL):
    s, d = _drafted(session, name, domain, mode=mode)
    approve_drafts(session, [d.id])
    return s, d


def test_send_batch_sends_eligible(session):
    s, d = _queued(session, "A", "a.io")
    t = _Recorder()
    results = send_batch(session, now=NOW, transport=t, settings=_settings())
    assert len(t.sent) == 1 and results[0].sent is True
    assert s.status == StartupStatus.SENT
    msg = session.scalars(select(Message)).one()
    assert msg.status == MessageStatus.SENT and msg.smtp_message_id


def test_send_batch_idempotent(session):
    _queued(session, "A", "a.io")
    t = _Recorder()
    send_batch(session, now=NOW, transport=t, settings=_settings())
    second = send_batch(session, now=NOW, transport=t, settings=_settings())
    assert second == []          # nothing eligible the second time
    assert len(t.sent) == 1


def test_send_batch_respects_pause(session):
    _queued(session, "A", "a.io")
    state_mod.pause(session, "manual")
    t = _Recorder()
    results = send_batch(session, now=NOW, transport=t, settings=_settings())
    assert results == [SendResult(0, False, "paused")] and t.sent == []


def test_send_batch_outside_window(session):
    _queued(session, "A", "a.io")
    t = _Recorder()
    results = send_batch(session, now=NOW, transport=t,
                         settings=_settings(send_start="12:00", send_end="13:00"))
    assert results == [SendResult(0, False, "outside_window")] and t.sent == []


def test_send_batch_dry_run_to_test_recipient(session):
    s, d = _queued(session, "A", "a.io")
    t = _Recorder()
    results = send_batch(session, now=NOW, transport=t,
                         settings=_settings(test_recipient="self@me.com"),
                         dry_run=True)
    assert results[0].sent is True
    assert t.sent[0]["To"] == "self@me.com"
    assert s.status == StartupStatus.QUEUED               # unchanged
    assert session.scalars(select(Message)).all() == []   # nothing persisted


def test_send_batch_cap_reached(session):
    _queued(session, "A", "a.io")
    _queued(session, "B", "b.io")
    t = _Recorder()
    results = send_batch(session, now=NOW, transport=t,
                         settings=_settings(ramp_daily_cap=1), limit=5)
    assert len([r for r in results if r.sent]) == 1 and len(t.sent) == 1


def test_send_failure_logs_and_autopauses(session):
    for name, dom in [("A", "a.io"), ("B", "b.io"), ("C", "c.io")]:
        _queued(session, name, dom)
    t = _Recorder(fail=True)
    send_batch(session, now=NOW, transport=t, settings=_settings(), limit=3)
    state = state_mod.ensure_state(session)
    assert state.paused and "3 consecutive" in state.paused_reason
    failed = session.scalars(select(Event).where(Event.kind == "send_failed")).all()
    assert len(failed) == 3


def test_force_bypasses_window(session):
    _queued(session, "A", "a.io")
    t = _Recorder()
    results = send_batch(session, now=NOW, transport=t,
                         settings=_settings(send_start="12:00", send_end="13:00"),
                         force=True)
    assert results[0].sent is True and len(t.sent) == 1


def test_send_test_emails_to_self(session):
    t = _Recorder()
    n = send_test_emails(session, transport=t,
                         settings=_settings(test_recipient="self@me.com"), count=3)
    assert n == 3 and len(t.sent) == 3
    assert all(m["To"] == "self@me.com" for m in t.sent)
    ev = session.scalars(select(Event).where(Event.kind == "test_send")).one()
    assert ev.payload["sent"] == 3
