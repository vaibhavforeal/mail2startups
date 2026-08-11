import shutil
from pathlib import Path

from sqlalchemy import select
from typer.testing import CliRunner

import app.cli as cli_mod
from app.cli import app
from app.db import get_engine, init_db, make_session
from app.draft.claude_draft import DraftPlan
from app.draft.service import DraftResult
from app.models import Contact, Draft, Startup, StartupStatus

runner = CliRunner()
FIXTURE = Path(__file__).parent / "fixtures" / "resume_min.yaml"


def _prepare(monkeypatch, tmp_path, *, with_resume=True):
    db = tmp_path / "test.db"
    monkeypatch.setenv("M2S_DB_PATH", str(db))
    if with_resume:
        resume = tmp_path / "resume.yaml"
        shutil.copy(FIXTURE, resume)
        monkeypatch.setenv("M2S_RESUME_PATH", str(resume))
    else:
        monkeypatch.setenv("M2S_RESUME_PATH", str(tmp_path / "missing.yaml"))
    runner.invoke(app, ["init-db"])
    return db


def test_draft_missing_resume_exits(monkeypatch, tmp_path):
    _prepare(monkeypatch, tmp_path, with_resume=False)
    result = runner.invoke(app, ["draft"])
    assert result.exit_code == 1
    assert "resume file not found" in result.output


def test_draft_reports_summary(monkeypatch, tmp_path):
    _prepare(monkeypatch, tmp_path)

    def fake_draft_all(session, **kwargs):
        return [DraftResult(1, True, "formal"), DraftResult(2, False, None)]

    monkeypatch.setattr(cli_mod, "draft_all", fake_draft_all)
    result = runner.invoke(app, ["draft", "--limit", "5"])
    assert result.exit_code == 0
    assert "processed=2" in result.output and "drafted=1" in result.output


def test_drafts_list_and_show(monkeypatch, tmp_path):
    db = _prepare(monkeypatch, tmp_path)
    engine = get_engine(db)
    init_db(engine)
    with make_session(engine) as s:
        startup = Startup(name="Globex", domain="globex.io", source="yc",
                          status=StartupStatus.DRAFTED)
        s.add(startup)
        s.commit()
        s.add(Draft(startup_id=startup.id, subject="Intern application",
                    body="Hello there", resume_pdf_path="out/resumes/x.pdf"))
        s.commit()
        draft_id = s.scalars(select(Draft.id)).one()

    listed = runner.invoke(app, ["drafts", "list"])
    assert listed.exit_code == 0 and "Globex" in listed.output

    shown = runner.invoke(app, ["drafts", "show", str(draft_id)])
    assert shown.exit_code == 0
    assert "Intern application" in shown.output and "Hello there" in shown.output


def test_draft_dry_run_writes_nothing(monkeypatch, tmp_path):
    db = _prepare(monkeypatch, tmp_path)
    engine = get_engine(db)
    init_db(engine)
    with make_session(engine) as s:
        startup = Startup(name="Globex", domain="globex.io", source="yc",
                          status=StartupStatus.ENRICHED)
        s.add(startup)
        s.commit()
        s.add(Contact(startup_id=startup.id, name="Priya", role="CTO",
                      email="priya@globex.io", found_via="scraped",
                      confidence=0.9, verified=True))
        s.commit()

    plan = DraftPlan(mode="formal", angle="ai", experience_ids=["e_intern"],
                     project_ids=["p_ai"], summary="s", skill_order=["Python"],
                     subject="Preview subject", body="b")
    monkeypatch.setattr(cli_mod, "draft_plan", lambda s, c, r: plan)

    result = runner.invoke(app, ["draft", "--dry-run"])
    assert result.exit_code == 0
    assert "Preview subject" in result.output
    with make_session(engine) as s:
        assert s.scalars(select(Draft)).all() == []  # dry-run persisted nothing
