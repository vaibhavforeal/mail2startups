import httpx
from sqlalchemy import select

from app.enrich import usage
from app.models import Contact, Event, Startup, StartupStatus
from app.scraper.email_finder import CandidateContact
from app.scraper.hunt import hunt_all, hunt_startup
from app.scraper.site_crawler import CrawledPage


def _make_startup(session, **kw):
    startup = Startup(name=kw.get("name", "Acme"), domain=kw.get("domain", "acme.com"),
                      source="yc")
    session.add(startup)
    session.commit()
    return startup


class FakeResolver:
    def resolve(self, domain, rdtype):
        return ["10 mail." + domain]  # every domain "has MX"


def test_hunt_startup_scrapes_and_enriches(session):
    startup = _make_startup(session)

    def fake_crawler(domain, client=None, paths=None):
        return [CrawledPage(
            url="https://acme.com/team",
            html="<h3>Jane Roe</h3><p>Founder &amp; CEO</p> Email jane@acme.com",
            status=200,
        )]

    result = hunt_startup(session, startup, crawler=fake_crawler,
                          founder_search=lambda name, **kw: [], resolver=FakeResolver())

    assert result.enriched is True and result.contacts_added >= 1
    session.refresh(startup)
    assert startup.status == StartupStatus.ENRICHED
    emails = {c.email for c in session.scalars(select(Contact)).all()}
    assert "jane@acme.com" in emails
    assert session.scalars(select(Event).where(Event.kind == "enriched")).first()


def test_hunt_gap_fills_founder_then_guesses(session):
    startup = _make_startup(session, name="StealthCo", domain="stealth.io")

    # No emails on the site, but a team page mentions nobody; founder search fills the gap.
    def fake_crawler(domain, client=None, paths=None):
        return [CrawledPage(url="https://stealth.io/", html="<p>Coming soon</p>", status=200)]

    result = hunt_startup(
        session, startup,
        crawler=fake_crawler,
        founder_search=lambda name, **kw: ["Priya Nair"],
        resolver=FakeResolver(),
    )
    assert result.contacts_added >= 1
    emails = {c.email for c in session.scalars(select(Contact)).all()}
    assert "priya.nair@stealth.io" in emails  # top pattern guess, MX-verified


def test_hunt_uses_enricher_only_as_last_resort(session):
    startup = _make_startup(session, name="Ghost", domain="ghost.io")
    calls = {"n": 0}

    class FakeEnricher:
        name = "hunter"
        def domain_search(self, domain):
            calls["n"] += 1
            return [CandidateContact(email="found@ghost.io", role="CEO",
                                     found_via="api", confidence=0.9, verified=True)]

    result = hunt_startup(
        session, startup,
        crawler=lambda domain, client=None, paths=None: [],   # nothing scraped
        founder_search=lambda name, **kw: [],                  # no founder names
        enricher=FakeEnricher(),
        resolver=FakeResolver(),
    )
    assert calls["n"] == 1  # enricher consulted because scraping + guessing found nothing
    assert result.contacts_added == 1
    assert result.enriched is True


def test_hunt_marks_scrape_failed_when_nothing_found(session):
    startup = _make_startup(session, name="Empty", domain="empty.io")
    result = hunt_startup(
        session, startup,
        crawler=lambda domain, client=None, paths=None: [],
        founder_search=lambda name, **kw: [],
        resolver=FakeResolver(),
    )
    assert result.enriched is False and result.contacts_added == 0
    session.refresh(startup)
    assert startup.status == StartupStatus.DISCOVERED
    assert session.scalars(select(Event).where(Event.kind == "scrape_failed")).first()


def test_hunt_all_processes_only_discovered(session):
    _make_startup(session, name="A", domain="a.io")
    done = _make_startup(session, name="B", domain="b.io")
    done.status = StartupStatus.ENRICHED
    session.commit()

    results = hunt_all(
        session, limit=10,
        crawler=lambda domain, client=None, paths=None: [],
        founder_search=lambda name, **kw: [],
        resolver=FakeResolver(),
    )
    assert len(results) == 1
    a_id = session.scalars(select(Startup.id).where(Startup.name == "A")).one()
    assert results[0].startup_id == a_id


def test_hunt_all_contains_provider_error(session):
    a = _make_startup(session, name="A", domain="a.io")
    b = _make_startup(session, name="B", domain="b.io")

    def flaky_founder_search(name, **kw):
        if name == "A":
            raise httpx.ConnectError("exa down")
        return []

    results = hunt_all(
        session, limit=10,
        crawler=lambda domain, client=None, paths=None: [],
        founder_search=flaky_founder_search,
        resolver=FakeResolver(),
    )
    # One provider failure no longer aborts the batch: both startups produced a result.
    assert len(results) == 2

    a_events = session.scalars(
        select(Event).where(Event.startup_id == a.id, Event.kind == "scrape_failed")
    ).all()
    assert any(e.payload["reason"] == "provider_error" for e in a_events)

    b_events = session.scalars(
        select(Event).where(Event.startup_id == b.id, Event.kind == "scrape_failed")
    ).all()
    assert [e.payload["reason"] for e in b_events] == ["no_contacts"]


def test_hunt_enricher_failure_does_not_burn_credit(session):
    _make_startup(session, name="Flaky", domain="flaky.io")

    class FailingEnricher:
        name = "hunter"
        def domain_search(self, domain):
            raise httpx.ConnectError("hunter down")

    hunt_all(
        session, limit=10,
        crawler=lambda domain, client=None, paths=None: [],
        founder_search=lambda name, **kw: [],
        enricher=FailingEnricher(),
        resolver=FakeResolver(),
    )
    # Credit is recorded only after a successful call, so the failed call burned nothing.
    assert usage.usage_this_month(session, "hunter") == 0
