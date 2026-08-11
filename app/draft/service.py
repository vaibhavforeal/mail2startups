from dataclasses import dataclass
from pathlib import Path

import anthropic
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.draft.claude_draft import DraftPlan, MalformedDraftError, draft_plan, unknown_ids
from app.draft.render import render_resume
from app.draft.resume_schema import Resume
from app.enrich.ranking import is_founder_role
from app.models import (
    Contact, Draft, DraftMode, DraftStatus, Event, Startup, StartupStatus,
)

# Same source ordering as Phase 2 ranking: scraped > api > pattern_guess > generic.
_SOURCE_RANK = {"scraped": 3, "api": 2, "pattern_guess": 1, "generic": 0}


@dataclass
class DraftResult:
    startup_id: int
    drafted: bool
    mode: str | None


def _contact_key(c: Contact) -> tuple:
    return (
        1 if is_founder_role(c.role) else 0,
        _SOURCE_RANK.get(c.found_via, 0),
        1 if c.verified else 0,
        c.confidence,
    )


def select_primary_contact(contacts: list[Contact]) -> Contact | None:
    usable = [c for c in contacts if c.email]
    return max(usable, key=_contact_key) if usable else None


def _log_failed(session: Session, startup_id: int, reason: str, **extra) -> None:
    session.add(Event(startup_id=startup_id, kind="draft_failed",
                      payload={"reason": reason, **extra}))
    session.commit()


def draft_startup(session: Session, startup: Startup, *, resume: Resume,
                  drafter=draft_plan, renderer=render_resume,
                  out_dir: Path = Path("out/resumes")) -> DraftResult:
    contacts = session.scalars(
        select(Contact).where(Contact.startup_id == startup.id)).all()
    contact = select_primary_contact(list(contacts))
    if contact is None:
        _log_failed(session, startup.id, "no_contact")
        return DraftResult(startup.id, False, None)

    try:
        plan: DraftPlan = drafter(startup, contact, resume)
    except MalformedDraftError as exc:
        _log_failed(session, startup.id, "malformed_response", detail=str(exc))
        return DraftResult(startup.id, False, None)

    bad = unknown_ids(plan, resume)
    if bad:
        _log_failed(session, startup.id, "invalid_id", ids=bad)
        return DraftResult(startup.id, False, None)

    pdf_path = None
    if plan.mode == "formal":
        pdf_path = str(renderer(plan, resume, startup.name, out_dir=out_dir))

    session.add(Draft(
        startup_id=startup.id, contact_id=contact.id, mode=DraftMode(plan.mode),
        subject=plan.subject, body=plan.body, resume_pdf_path=pdf_path,
        status=DraftStatus.PENDING_REVIEW,
    ))
    startup.status = StartupStatus.DRAFTED
    session.add(Event(startup_id=startup.id, kind="drafted",
                      payload={"mode": plan.mode, "angle": plan.angle}))
    session.commit()
    return DraftResult(startup.id, True, plan.mode)


def draft_all(session: Session, *, limit: int = 50, resume: Resume,
              drafter=draft_plan, renderer=render_resume,
              out_dir: Path = Path("out/resumes")) -> list[DraftResult]:
    startups = session.scalars(
        select(Startup)
        .where(Startup.status == StartupStatus.ENRICHED,
               Startup.id.not_in(select(Draft.startup_id)))
        .limit(limit)
    ).all()
    results: list[DraftResult] = []
    for startup in startups:
        sid = startup.id  # capture before any rollback expires the instance
        try:
            results.append(draft_startup(
                session, startup, resume=resume, drafter=drafter,
                renderer=renderer, out_dir=out_dir))
        except (anthropic.AnthropicError, ValueError) as exc:
            session.rollback()
            _log_failed(session, sid, "provider_error", detail=str(exc))
            results.append(DraftResult(sid, False, None))
    return results
