from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from typer.testing import CliRunner

import app.cli as cli_mod
from app.cli import app
from app.db import get_engine, init_db, make_session
from app.followup.claude_followup import FollowupPlan
from app.models import (
    Contact, Draft, DraftMode, DraftStatus, Message, MessageStatus,
    MessageType, Startup, StartupStatus,
)

runner = CliRunner()
FIXTURE = Path(__file__).parent / "fixtures" / "resume_min.yaml"


def _prepare(monkeypatch, tmp_path):
    db = tmp_path / "test.db"
    monkeypatch.setenv("M2S_DB_PATH", str(db))
    monkeypatch.setenv("M2S_RESUME_PATH", str(FIXTURE))
    runner.invoke(app, ["init-db"])
    return db


def _seed_silent(db):
    engine = get_engine(db)
    init_db(engine)
    with make_session(engine) as s:
        st = Startup(name="A", domain="a.io", source="yc",
                     status=StartupStatus.SENT)
        s.add(st); s.commit()
        c = Contact(startup_id=st.id, name="C", role="CTO", email="c@a.io",
                    found_via="scraped", confidence=0.9, verified=True)
        s.add(c); s.commit()
        d = Draft(startup_id=st.id, contact_id=c.id, type=MessageType.INITIAL,
                  mode=DraftMode.FORMAL, subject="Intern application",
                  body="Hello", status=DraftStatus.APPROVED)
        s.add(d); s.commit()
        old = datetime.now(timezone.utc) - timedelta(days=10)
        s.add(Message(draft_id=d.id, type=MessageType.INITIAL, sent_at=old,
                      smtp_message_id="<i@m>", status=MessageStatus.SENT))
        s.commit()


def _fake_gen(settings):
    return lambda startup, resume, subject, body: FollowupPlan(body="bump")


def test_followups_drafts(monkeypatch, tmp_path):
    db = _prepare(monkeypatch, tmp_path)
    _seed_silent(db)
    monkeypatch.setattr(cli_mod, "_build_followup_generator", _fake_gen)
    out = runner.invoke(app, ["followups"])
    assert out.exit_code == 0 and "drafted=1" in out.output
    with make_session(get_engine(db)) as s:
        fu = s.scalars(
            select(Draft).where(Draft.type == MessageType.FOLLOWUP)).one()
        assert fu.subject == "Re: Intern application"
        assert fu.status == DraftStatus.PENDING_REVIEW


def test_followups_dry_run_writes_nothing(monkeypatch, tmp_path):
    db = _prepare(monkeypatch, tmp_path)
    _seed_silent(db)
    monkeypatch.setattr(cli_mod, "_build_followup_generator", _fake_gen)
    out = runner.invoke(app, ["followups", "--dry-run"])
    assert out.exit_code == 0 and "drafted=1" in out.output
    with make_session(get_engine(db)) as s:
        assert s.scalars(
            select(Draft).where(Draft.type == MessageType.FOLLOWUP)).all() == []
