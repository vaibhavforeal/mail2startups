from sqlalchemy import select
from typer.testing import CliRunner

import app.cli as cli_mod
from app.cli import app
from app.db import get_engine, init_db, make_session
from app.models import (
    Contact, Draft, DraftMode, DraftStatus, Message, MessageStatus,
    Startup, StartupStatus,
)

runner = CliRunner()


def _prepare(monkeypatch, tmp_path):
    db = tmp_path / "test.db"
    monkeypatch.setenv("M2S_DB_PATH", str(db))
    monkeypatch.setenv("M2S_SMTP_USER", "me@d.com")
    monkeypatch.setenv("M2S_SMTP_PASSWORD", "secret")
    monkeypatch.setenv("M2S_FROM_EMAIL", "me@d.com")
    monkeypatch.setenv("M2S_TEST_RECIPIENT", "self@me.com")
    runner.invoke(app, ["init-db"])
    return db


def _seed_drafted(db, name, domain):
    engine = get_engine(db)
    init_db(engine)
    with make_session(engine) as s:
        startup = Startup(name=name, domain=domain, source="yc",
                          status=StartupStatus.DRAFTED)
        s.add(startup); s.commit()
        c = Contact(startup_id=startup.id, name="C", role="CTO", email=f"c@{domain}",
                    found_via="scraped", confidence=0.9, verified=True)
        s.add(c); s.commit()
        d = Draft(startup_id=startup.id, contact_id=c.id, mode=DraftMode.FORMAL,
                  subject="Hi", body="Hello", resume_pdf_path=None,
                  status=DraftStatus.PENDING_REVIEW)
        s.add(d); s.commit()
        return s.scalars(select(Draft.id)).first()


class _Recorder:
    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)
        return msg["Message-ID"]


def test_approve_then_send(monkeypatch, tmp_path):
    db = _prepare(monkeypatch, tmp_path)
    draft_id = _seed_drafted(db, "A", "a.io")

    approved = runner.invoke(app, ["approve", str(draft_id)])
    assert approved.exit_code == 0 and "approved 1" in approved.output

    rec = _Recorder()
    monkeypatch.setattr(cli_mod, "_build_transport", lambda settings: rec)
    sent = runner.invoke(app, ["send", "--force"])
    assert sent.exit_code == 0 and "sent=1" in sent.output
    assert len(rec.sent) == 1
    with make_session(get_engine(db)) as s:
        assert s.scalars(select(Message)).one().status == MessageStatus.SENT


def test_reject_marks_dead(monkeypatch, tmp_path):
    db = _prepare(monkeypatch, tmp_path)
    draft_id = _seed_drafted(db, "A", "a.io")
    out = runner.invoke(app, ["reject", str(draft_id)])
    assert out.exit_code == 0 and "rejected 1" in out.output
    with make_session(get_engine(db)) as s:
        assert s.scalars(select(Startup)).one().status == StartupStatus.DEAD


def test_send_requires_credentials(monkeypatch, tmp_path):
    db = tmp_path / "t.db"
    monkeypatch.setenv("M2S_DB_PATH", str(db))
    monkeypatch.setenv("M2S_SMTP_USER", "")       # explicit "" beats any .env file value
    monkeypatch.setenv("M2S_SMTP_PASSWORD", "")
    runner.invoke(app, ["init-db"])
    out = runner.invoke(app, ["send"])
    assert out.exit_code == 1 and "M2S_SMTP_USER" in out.output


def test_pause_and_resume(monkeypatch, tmp_path):
    from app.send.state import ensure_state
    db = _prepare(monkeypatch, tmp_path)
    assert runner.invoke(app, ["pause"]).exit_code == 0
    with make_session(get_engine(db)) as s:
        assert ensure_state(s).paused
    runner.invoke(app, ["resume"])
    with make_session(get_engine(db)) as s:
        assert not ensure_state(s).paused


def test_test_send(monkeypatch, tmp_path):
    _prepare(monkeypatch, tmp_path)
    rec = _Recorder()
    monkeypatch.setattr(cli_mod, "_build_transport", lambda settings: rec)
    out = runner.invoke(app, ["test-send", "--count", "3"])
    assert out.exit_code == 0 and "sent 3/3" in out.output
    assert len(rec.sent) == 3


def test_preflight(monkeypatch, tmp_path):
    _prepare(monkeypatch, tmp_path)
    monkeypatch.setenv("M2S_DKIM_SELECTOR", "sel")

    def fake_resolve(name, rtype):
        return {
            "d.com": ["v=spf1 ~all"],
            "_dmarc.d.com": ["v=DMARC1"],
            "sel._domainkey.d.com": ["v=DKIM1; p=x"],
        }.get(name, [])

    monkeypatch.setattr(cli_mod, "_dns_resolve", fake_resolve)
    out = runner.invoke(app, ["preflight"])
    assert out.exit_code == 0 and "all checks passed" in out.output
