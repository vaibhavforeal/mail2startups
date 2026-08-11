from sqlalchemy import select

from app.models import Contact, Event, Startup
from app.scraper.ingest import ingest_records
from app.scraper.sources.base import StartupRecord


def _records():
    return [
        StartupRecord(name="Acme", website="https://www.acme.com", source="yc",
                      contact_emails=["hello@acme.com"], founder_names=["Jane Roe"]),
        StartupRecord(name="Beta Labs", website="https://beta.io", source="yc"),
    ]


def test_ingest_inserts(session):
    result = ingest_records(session, _records())
    assert result.added == 2 and result.skipped == 0
    acme = session.scalars(select(Startup).where(Startup.domain == "acme.com")).one()
    assert acme.founder_names == ["Jane Roe"]
    contact = session.scalars(select(Contact).where(Contact.startup_id == acme.id)).one()
    assert contact.email == "hello@acme.com" and contact.found_via == "scraped"
    assert session.scalars(select(Event).where(Event.kind == "discovered")).all()


def test_ingest_is_idempotent(session):
    ingest_records(session, _records())
    result = ingest_records(session, _records())
    assert result.added == 0 and result.skipped == 2
    assert len(session.scalars(select(Startup)).all()) == 2


def test_dedup_across_sources_by_domain(session):
    ingest_records(session, [StartupRecord(name="Acme", website="https://acme.com", source="yc")])
    result = ingest_records(session, [StartupRecord(name="ACME Inc", website="http://www.acme.com/", source="csv")])
    assert result.added == 0 and result.skipped == 1


def test_no_domain_dedupes_by_name(session):
    ingest_records(session, [StartupRecord(name="Stealth Co", source="listicle")])
    result = ingest_records(session, [StartupRecord(name="stealth co", source="listicle")])
    assert result.added == 0 and result.skipped == 1
