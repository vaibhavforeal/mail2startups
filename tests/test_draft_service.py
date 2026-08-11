from pathlib import Path

import anthropic
from sqlalchemy import select

from app.draft.claude_draft import DraftPlan, MalformedDraftError
from app.draft.resume_schema import load_resume
from app.draft.service import draft_all, select_primary_contact
from app.models import Contact, Draft, Event, Startup, StartupStatus

FIXTURE = Path(__file__).parent / "fixtures" / "resume_min.yaml"


def _resume():
    return load_resume(FIXTURE)


def _enriched(session, name, domain, *, found_via="scraped", role="CTO"):
    s = Startup(name=name, domain=domain, source="yc", status=StartupStatus.ENRICHED)
    session.add(s)
    session.commit()
    session.add(Contact(startup_id=s.id, name="Contact " + name, role=role,
                        email=f"c@{domain}", found_via=found_via, confidence=0.9,
                        verified=True))
    session.commit()
    return s


def _plan(mode="formal", **over):
    base = dict(mode=mode, angle="ai", experience_ids=["e_intern"],
                project_ids=["p_ai"], summary="s", skill_order=["Python"],
                subject="Intern application", body="hi")
    base.update(over)
    return DraftPlan(**base)


def _renderer(plan, resume, startup_name, *, out_dir):
    return Path(out_dir) / f"{startup_name}.pdf"


def test_select_primary_prefers_founder_then_source():
    generic = Contact(role="", email="a@x.io", found_via="generic", confidence=0.5, verified=False)
    founder = Contact(role="CEO", email="b@x.io", found_via="scraped", confidence=0.8, verified=True)
    assert select_primary_contact([generic, founder]) is founder
    assert select_primary_contact([]) is None


def test_draft_all_only_enriched_without_draft(session):
    session.add(Startup(name="Disc", domain="d.io", source="yc",
                        status=StartupStatus.DISCOVERED))
    _enriched(session, "Fresh", "fresh.io")
    already = _enriched(session, "Done", "done.io")
    session.add(Draft(startup_id=already.id, subject="x", body="y"))
    session.commit()

    results = draft_all(session, resume=_resume(),
                        drafter=lambda s, c, r: _plan(), renderer=_renderer)

    assert len(results) == 1 and results[0].drafted is True
    fresh = session.scalars(select(Startup).where(Startup.name == "Fresh")).one()
    assert fresh.status == StartupStatus.DRAFTED
    draft = session.scalars(select(Draft).where(Draft.startup_id == fresh.id)).one()
    assert draft.resume_pdf_path is not None  # formal → PDF


def test_draft_all_casual_has_no_pdf(session):
    _enriched(session, "Casual", "casual.io")
    draft_all(session, resume=_resume(),
              drafter=lambda s, c, r: _plan(mode="casual"), renderer=_renderer)
    draft = session.scalars(select(Draft)).one()
    assert draft.mode.value == "casual" and draft.resume_pdf_path is None


def test_draft_all_contains_malformed(session):
    a = _enriched(session, "A", "a.io")
    _enriched(session, "B", "b.io")

    def drafter(s, c, r):
        if s.name == "A":
            raise MalformedDraftError("bad")
        return _plan()

    results = draft_all(session, resume=_resume(), drafter=drafter, renderer=_renderer)
    assert len(results) == 2
    a_events = session.scalars(
        select(Event).where(Event.startup_id == a.id, Event.kind == "draft_failed")).all()
    assert any(e.payload["reason"] == "malformed_response" for e in a_events)
    assert session.scalars(select(Draft)).all()  # B still drafted


def test_draft_all_contains_invalid_id(session):
    s = _enriched(session, "Ghosty", "ghost.io")
    draft_all(session, resume=_resume(),
              drafter=lambda st, c, r: _plan(project_ids=["p_ai", "ghost"]),
              renderer=_renderer)
    events = session.scalars(
        select(Event).where(Event.startup_id == s.id, Event.kind == "draft_failed")).all()
    assert any(e.payload["reason"] == "invalid_id" for e in events)
    assert session.scalars(select(Draft)).all() == []  # guardrail blocked the draft


def test_draft_all_contains_provider_error(session):
    a = _enriched(session, "A", "a.io")
    _enriched(session, "B", "b.io")

    def drafter(s, c, r):
        if s.name == "A":
            raise anthropic.AnthropicError("api down")
        return _plan()

    results = draft_all(session, resume=_resume(), drafter=drafter, renderer=_renderer)
    assert len(results) == 2
    a_events = session.scalars(
        select(Event).where(Event.startup_id == a.id, Event.kind == "draft_failed")).all()
    assert any(e.payload["reason"] == "provider_error" for e in a_events)
