from datetime import datetime, timezone

from sqlalchemy import select
from typer.testing import CliRunner

import app.cli as cli_mod
from app.cli import app
from app.db import get_engine, init_db, make_session
from app.inbox.imap_client import FetchedMessage
from app.models import (
    Contact, Draft, DraftStatus, InboxMessage, Message, MessageStatus,
    MessageType, ReplyLabel, Startup, StartupStatus,
)

runner = CliRunner()


def _prepare(monkeypatch, tmp_path):
    db = tmp_path / "test.db"
    monkeypatch.setenv("M2S_DB_PATH", str(db))
    monkeypatch.setenv("M2S_IMAP_USER", "me@d.com")
    monkeypatch.setenv("M2S_IMAP_PASSWORD", "secret")
    runner.invoke(app, ["init-db"])
    return db


def _seed_sent(db, smtp_id):
    engine = get_engine(db); init_db(engine)
    with make_session(engine) as s:
        st = Startup(name="A", domain="a.io", source="yc",
                     status=StartupStatus.SENT)
        s.add(st); s.commit()
        c = Contact(startup_id=st.id, name="C", role="CTO", email="c@a.io",
                    found_via="scraped", confidence=0.9, verified=True)
        s.add(c); s.commit()
        d = Draft(startup_id=st.id, contact_id=c.id, subject="Hi", body="Hello",
                  resume_pdf_path=None, status=DraftStatus.APPROVED)
        s.add(d); s.commit()
        m = Message(draft_id=d.id, type=MessageType.INITIAL,
                    sent_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
                    smtp_message_id=smtp_id, status=MessageStatus.SENT)
        s.add(m); s.commit()


class _FakeImap:
    def __init__(self, messages):
        self._messages = messages

    def fetch_new(self, mailbox, since_uid, uidvalidity):
        return 1, list(self._messages)


def test_inbox_records_reply(monkeypatch, tmp_path):
    db = _prepare(monkeypatch, tmp_path)
    _seed_sent(db, "<out-1@d.com>")
    fm = FetchedMessage(uid=10, imap_message_id="<in-1@a.io>", from_addr="c@a.io",
                        subject="Re: Hi", in_reply_to="<out-1@d.com>",
                        references=[], body_text="great!", raw="", received_at=None)
    monkeypatch.setattr(cli_mod, "_build_imap", lambda settings: _FakeImap([fm]))
    monkeypatch.setattr(cli_mod, "_build_classifier",
                        lambda settings: (lambda text: ReplyLabel.INTERESTED))
    out = runner.invoke(app, ["inbox"])
    assert out.exit_code == 0
    assert "fetched=1" in out.output and "replies=1" in out.output
    assert "bounces=0" in out.output
    with make_session(get_engine(db)) as s:
        assert s.scalars(select(Startup)).one().status == StartupStatus.REPLIED
        assert s.scalars(select(InboxMessage)).one().label == ReplyLabel.INTERESTED


def test_inbox_requires_credentials(monkeypatch, tmp_path):
    db = tmp_path / "t.db"
    monkeypatch.setenv("M2S_DB_PATH", str(db))
    monkeypatch.setenv("M2S_IMAP_USER", "")
    monkeypatch.setenv("M2S_IMAP_PASSWORD", "")
    monkeypatch.setenv("M2S_SMTP_USER", "")
    monkeypatch.setenv("M2S_SMTP_PASSWORD", "")
    runner.invoke(app, ["init-db"])
    out = runner.invoke(app, ["inbox"])
    assert out.exit_code == 1 and "M2S_IMAP_USER" in out.output
