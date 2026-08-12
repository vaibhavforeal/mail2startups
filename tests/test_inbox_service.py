from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import select

from app.inbox.imap_client import FetchedMessage
from app.inbox.service import poll_inbox
from app.models import (
    Contact, Draft, DraftStatus, Event, InboxKind, InboxMessage, Message,
    MessageStatus, MessageType, ReplyLabel, Startup, StartupStatus,
)
from app.send.state import ensure_state

NOW = datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc)


def _settings(**over):
    base = dict(imap_mailbox="INBOX", no_response_days=14)
    base.update(over)
    return SimpleNamespace(**base)


def _interested(text):
    return ReplyLabel.INTERESTED


def _seed_sent(session, name, domain, smtp_id, *, sent_at, verified=True):
    s = Startup(name=name, domain=domain, source="yc", status=StartupStatus.SENT)
    session.add(s); session.commit()
    c = Contact(startup_id=s.id, name="C", role="CTO", email=f"c@{domain}",
                found_via="scraped", confidence=0.9, verified=verified)
    session.add(c); session.commit()
    d = Draft(startup_id=s.id, contact_id=c.id, subject="Hi", body="Hello",
              resume_pdf_path=None, status=DraftStatus.APPROVED)
    session.add(d); session.commit()
    m = Message(draft_id=d.id, type=MessageType.INITIAL, sent_at=sent_at,
                smtp_message_id=smtp_id, status=MessageStatus.SENT)
    session.add(m); session.commit()
    return s, c, d, m


def _fetched(uid, message_id, *, from_addr="", subject="", in_reply_to="",
             references=None, body_text="", raw="", received_at=None):
    return FetchedMessage(
        uid=uid, imap_message_id=message_id, from_addr=from_addr, subject=subject,
        in_reply_to=in_reply_to, references=references or [], body_text=body_text,
        raw=raw, received_at=received_at)


class FakeImap:
    def __init__(self, messages, uidvalidity=1):
        self._messages, self._uidvalidity = messages, uidvalidity

    def fetch_new(self, mailbox, since_uid, uidvalidity):
        return self._uidvalidity, list(self._messages)


def test_reply_advances_startup_and_records(session):
    s, c, d, m = _seed_sent(session, "A", "a.io", "<out-1@d.com>", sent_at=NOW)
    fm = _fetched(10, "<in-1@a.io>", from_addr="c@a.io", subject="Re: Hi",
                  in_reply_to="<out-1@d.com>", body_text="Sounds great!")
    res = poll_inbox(session, imap=FakeImap([fm]), classifier=_interested,
                     now=NOW, settings=_settings())
    assert res.replies == 1 and res.bounces == 0 and res.fetched == 1
    session.refresh(s); session.refresh(m)
    assert s.status == StartupStatus.REPLIED
    assert m.status == MessageStatus.REPLIED
    im = session.scalars(select(InboxMessage)).one()
    assert im.kind == InboxKind.REPLY and im.label == ReplyLabel.INTERESTED
    assert im.matched_message_id == "<out-1@d.com>"
    ev = session.scalars(select(Event).where(Event.kind == "reply")).one()
    assert ev.payload["label"] == "interested"


def test_bounce_marks_terminal_and_demotes_contact(session):
    s, c, d, m = _seed_sent(session, "B", "b.io", "<out-2@d.com>", sent_at=NOW)
    fm = _fetched(11, "<dsn-1@mail>", from_addr="mailer-daemon@b.io",
                  subject="Undelivered Mail Returned to Sender",
                  raw="550 user unknown; original message <out-2@d.com> failed",
                  body_text="delivery failed")
    res = poll_inbox(session, imap=FakeImap([fm]), classifier=_interested,
                     now=NOW, settings=_settings())
    assert res.bounces == 1 and res.replies == 0
    session.refresh(s); session.refresh(c); session.refresh(m)
    assert s.status == StartupStatus.BOUNCED
    assert m.status == MessageStatus.BOUNCED
    assert c.verified is False
    im = session.scalars(select(InboxMessage)).one()
    assert im.kind == InboxKind.BOUNCE and im.label is None
    assert session.scalars(select(Event).where(Event.kind == "bounce")).one()


def test_no_response_sweeps_aged_and_leaves_fresh(session):
    old_s, *_ = _seed_sent(session, "Old", "old.io", "<out-old@d.com>",
                           sent_at=NOW - timedelta(days=20))
    fresh_s, *_ = _seed_sent(session, "New", "new.io", "<out-new@d.com>",
                             sent_at=NOW - timedelta(days=3))
    res = poll_inbox(session, imap=FakeImap([]), classifier=_interested,
                     now=NOW, settings=_settings())
    assert res.no_response == 1
    session.refresh(old_s); session.refresh(fresh_s)
    assert old_s.status == StartupStatus.NO_RESPONSE
    assert fresh_s.status == StartupStatus.SENT
    assert session.scalars(
        select(Event).where(Event.kind == "no_response")).one()


def test_second_poll_is_noop(session):
    _seed_sent(session, "A", "a.io", "<out-1@d.com>", sent_at=NOW)
    fm = _fetched(10, "<in-1@a.io>", from_addr="c@a.io",
                  in_reply_to="<out-1@d.com>", body_text="hi")
    imap = FakeImap([fm])
    poll_inbox(session, imap=imap, classifier=_interested, now=NOW,
               settings=_settings())
    before = len(session.scalars(select(InboxMessage)).all())
    res2 = poll_inbox(session, imap=imap, classifier=_interested, now=NOW,
                      settings=_settings())
    after = len(session.scalars(select(InboxMessage)).all())
    assert res2.replies == 0 and before == after == 1


def test_dry_run_mutates_nothing(session):
    s, *_ = _seed_sent(session, "A", "a.io", "<out-1@d.com>", sent_at=NOW)
    fm = _fetched(10, "<in-1@a.io>", from_addr="c@a.io",
                  in_reply_to="<out-1@d.com>", body_text="hi")
    res = poll_inbox(session, imap=FakeImap([fm]), classifier=_interested,
                     now=NOW, settings=_settings(), dry_run=True)
    assert res.replies == 1                       # counted
    session.refresh(s)
    assert s.status == StartupStatus.SENT          # unchanged
    assert session.scalars(select(InboxMessage)).all() == []
    assert ensure_state(session).last_imap_uid == 0
