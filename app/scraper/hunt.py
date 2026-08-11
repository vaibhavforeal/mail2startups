from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enrich import usage
from app.enrich.founder_search import find_founder_names
from app.enrich.ranking import rank_contacts
from app.enrich.verify import verify_candidates
from app.models import Contact, Event, Startup, StartupStatus
from app.scraper.email_finder import (
    CandidateContact,
    extract_emails,
    extract_people,
    guess_email_candidates,
)
from app.scraper.site_crawler import crawl_site

# A candidate is "good enough" to skip paid enrichment only when it was scraped
# from the site or returned by a paid API. Pattern guesses never count, so a
# guess-only startup still triggers enrichment (by product decision — a guess is
# not a confirmed mailbox).
_GOOD_SOURCES = {"scraped", "api"}


@dataclass
class HuntResult:
    startup_id: int
    contacts_added: int
    enriched: bool


def _has_good_contact(candidates: list[CandidateContact]) -> bool:
    return any(c.found_via in _GOOD_SOURCES and c.email for c in candidates)


def hunt_startup(session: Session, startup: Startup, *, crawler=crawl_site,
                 founder_search=find_founder_names, enricher=None, resolver=None,
                 monthly_limit: int = 25) -> HuntResult:
    if not startup.domain:
        session.add(Event(startup_id=startup.id, kind="scrape_failed",
                          payload={"reason": "no_domain"}))
        session.commit()
        return HuntResult(startup_id=startup.id, contacts_added=0, enriched=False)

    candidates: list[CandidateContact] = []
    people = []

    # 1. Crawl + extract scraped emails and team people.
    for page in crawler(startup.domain):
        candidates.extend(extract_emails(page.html))
        people.extend(extract_people(page.html))

    # 2. Founder names: from team pages, else stored, else web gap-fill.
    founder_names = [p.name for p in people]
    if not founder_names:
        founder_names = list(startup.founder_names or [])
    if not founder_names:
        founder_names = founder_search(startup.name)

    person_roles = {p.name: p.role for p in people}

    # 3. Pattern-guess for each known person.
    for name in founder_names:
        candidates.extend(
            guess_email_candidates(name, startup.domain, role=person_roles.get(name, "Founder"))
        )

    # 4. Verify domains via MX.
    verify_candidates(candidates, resolver=resolver)

    # 5. Paid enrichment only if scraping + guessing produced nothing usable.
    if enricher is not None and not _has_good_contact(candidates):
        if usage.remaining(session, enricher.name, monthly_limit) > 0:
            found = enricher.domain_search(startup.domain)
            usage.record_call(session, enricher.name)
            candidates.extend(found)

    # 6. Rank and persist (dedupe against existing contacts for this startup).
    ranked = rank_contacts([c for c in candidates if c.email])
    existing = {
        c.email for c in session.scalars(
            select(Contact).where(Contact.startup_id == startup.id)
        )
    }
    added = 0
    for candidate in ranked:
        if candidate.email in existing:
            continue
        existing.add(candidate.email)
        session.add(Contact(
            startup_id=startup.id,
            name=candidate.name,
            role=candidate.role,
            email=candidate.email,
            found_via=candidate.found_via,
            confidence=candidate.confidence,
            verified=candidate.verified,
        ))
        added += 1

    if added > 0:
        startup.status = StartupStatus.ENRICHED
        session.add(Event(startup_id=startup.id, kind="enriched",
                          payload={"contacts_added": added}))
    else:
        session.add(Event(startup_id=startup.id, kind="scrape_failed",
                          payload={"reason": "no_contacts"}))
    session.commit()
    return HuntResult(startup_id=startup.id, contacts_added=added, enriched=added > 0)


def hunt_all(session: Session, *, limit: int = 50, crawler=crawl_site,
             founder_search=find_founder_names, enricher=None, resolver=None,
             monthly_limit: int = 25) -> list[HuntResult]:
    startups = session.scalars(
        select(Startup)
        .where(Startup.status == StartupStatus.DISCOVERED, Startup.domain.is_not(None))
        .limit(limit)
    ).all()
    results = []
    for startup in startups:
        startup_id = startup.id  # capture before any rollback can expire the instance
        try:
            results.append(hunt_startup(
                session, startup, crawler=crawler, founder_search=founder_search,
                enricher=enricher, resolver=resolver, monthly_limit=monthly_limit,
            ))
        except (httpx.HTTPError, ValueError) as exc:
            # ValueError covers json.JSONDecodeError from a provider returning a
            # 200 with a non-JSON body; contain any single provider fault so it
            # can never abort the whole batch.
            session.rollback()
            session.add(Event(startup_id=startup_id, kind="scrape_failed",
                              payload={"reason": "provider_error", "detail": str(exc)}))
            session.commit()
            results.append(HuntResult(startup_id=startup_id, contacts_added=0, enriched=False))
    return results
