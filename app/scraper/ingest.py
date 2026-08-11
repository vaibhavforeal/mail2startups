from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Contact, Event, Startup
from app.scraper.sources.base import StartupRecord


@dataclass
class IngestResult:
    added: int = 0
    skipped: int = 0


def _exists(session: Session, record: StartupRecord) -> bool:
    if record.domain:
        q = select(Startup.id).where(Startup.domain == record.domain)
    else:
        q = select(Startup.id).where(
            Startup.domain.is_(None), func.lower(Startup.name) == record.name.lower()
        )
    return session.scalars(q).first() is not None


def ingest_records(session: Session, records: list[StartupRecord]) -> IngestResult:
    result = IngestResult()
    for record in records:
        if _exists(session, record):
            result.skipped += 1
            continue
        startup = Startup(
            name=record.name,
            domain=record.domain,
            website=record.website,
            source=record.source,
            location=record.location,
            industry=record.industry,
            description=record.description,
            team_size=record.team_size,
            founder_names=list(record.founder_names),
        )
        session.add(startup)
        session.flush()
        for email in record.contact_emails:
            session.add(Contact(startup_id=startup.id, email=email,
                                found_via="scraped", confidence=0.5))
        session.add(Event(startup_id=startup.id, kind="discovered",
                          payload={"source": record.source}))
        result.added += 1
    session.commit()
    return result
