import types
from datetime import datetime, timezone

from sqlalchemy import select

from app.models import (
    Contact, Draft, DraftMode, DraftStatus, Message, MessageStatus,
    MessageType, Startup, StartupStatus,
)
from app.send.service import send_batch
from app.send.smtp_client import build_email

NOW = datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc)


def _settings(**over):
    base = dict(from_email="me@d.com", from_name="Me", smtp_user="me@d.com",
                test_recipient="me@d.com", send_start="00:00", send_end="23:59",
                send_timezone="Asia/Kolkata", daily_cap=30, ramp_daily_cap=15,
                ramp_days=7)
    base.update(over)
    return types.SimpleNamespace(**base)


class _Recorder:
    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)
        return msg["Message-ID"]


def test_build_email_sets_threading_headers():
    msg = build_email(from_email="me@d.com", from_name="Me", to="x@y.io",
                      subject="Re: Hi", body="bump", pdf_path=None,
                      in_reply_to="<init@m>", references="<init@m>")
    assert msg["In-Reply-To"] == "<init@m>"
    assert msg["References"] == "<init@m>"


def test_build_email_no_threading_by_default():
    msg = build_email(from_email="me@d.com", from_name="Me", to="x@y.io",
                      subject="Hi", body="hello", pdf_path=None)
    assert msg["In-Reply-To"] is None
    assert msg["References"] is None


def _followup_ready(session, name, domain, init_msg_id="<init@m>"):
    s = Startup(name=name, domain=domain, source="yc", status=StartupStatus.QUEUED)
    session.add(s); session.commit()
    c = Contact(startup_id=s.id, name="C", role="CTO", email=f"c@{domain}",
                found_via="scraped", confidence=0.9, verified=True)
    session.add(c); session.commit()
    init = Draft(startup_id=s.id, contact_id=c.id, type=MessageType.INITIAL,
                 mode=DraftMode.FORMAL, subject="Intern application", body="Hello",
                 status=DraftStatus.APPROVED)
    session.add(init); session.commit()
    session.add(Message(draft_id=init.id, type=MessageType.INITIAL, sent_at=NOW,
                        smtp_message_id=init_msg_id, status=MessageStatus.SENT))
    fu = Draft(startup_id=s.id, contact_id=c.id, type=MessageType.FOLLOWUP,
               mode=DraftMode.CASUAL, subject="Re: Intern application",
               body="bump", resume_pdf_path=None, status=DraftStatus.APPROVED)
    session.add(fu); session.commit()
    return s, init, fu


def test_send_followup_threads_and_types(session):
    s, init, fu = _followup_ready(session, "A", "a.io")
    t = _Recorder()
    results = send_batch(session, now=NOW, transport=t, settings=_settings(),
                         force=True)
    assert results[0].sent is True and results[0].draft_id == fu.id
    sent_msg = t.sent[0]
    assert sent_msg["In-Reply-To"] == "<init@m>"
    assert sent_msg["References"] == "<init@m>"
    assert sent_msg["Subject"] == "Re: Intern application"
    fu_msg = session.scalars(
        select(Message).where(Message.type == MessageType.FOLLOWUP)).one()
    assert fu_msg.status == MessageStatus.SENT
    assert s.status == StartupStatus.SENT


def test_sent_initial_not_re_selected_only_followup_sends(session):
    s, init, fu = _followup_ready(session, "B", "b.io")
    t = _Recorder()
    send_batch(session, now=NOW, transport=t, settings=_settings(),
               force=True, limit=5)
    assert len(t.sent) == 1
    assert t.sent[0]["Subject"] == "Re: Intern application"


def test_approve_skips_replied_startup(session):
    from app.send.service import approve_drafts
    s = Startup(name="R", domain="r.io", source="yc", status=StartupStatus.SENT)
    session.add(s); session.commit()
    c = Contact(startup_id=s.id, name="C", role="CTO", email="c@r.io",
                found_via="scraped", confidence=0.9, verified=True)
    session.add(c); session.commit()
    init = Draft(startup_id=s.id, contact_id=c.id, type=MessageType.INITIAL,
                 mode=DraftMode.FORMAL, subject="Intern application", body="Hello",
                 status=DraftStatus.APPROVED)
    session.add(init); session.commit()
    session.add(Message(draft_id=init.id, type=MessageType.INITIAL, sent_at=NOW,
                        smtp_message_id="<init@m>", status=MessageStatus.SENT))
    fu = Draft(startup_id=s.id, contact_id=c.id, type=MessageType.FOLLOWUP,
               mode=DraftMode.CASUAL, subject="Re: Intern application",
               body="bump", resume_pdf_path=None, status=DraftStatus.PENDING_REVIEW)
    session.add(fu); session.commit()
    s.status = StartupStatus.REPLIED
    session.commit()
    approved = approve_drafts(session, [fu.id])
    assert approved == 0
    session.refresh(fu)
    assert fu.status == DraftStatus.PENDING_REVIEW
    session.refresh(s)
    assert s.status == StartupStatus.REPLIED
    t = _Recorder()
    results = send_batch(session, now=NOW, transport=t, settings=_settings(),
                         force=True, limit=5)
    assert len(t.sent) == 0
