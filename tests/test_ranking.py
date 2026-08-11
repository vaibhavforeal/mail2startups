from app.enrich.ranking import is_founder_role, rank_contacts
from app.scraper.email_finder import CandidateContact


def test_is_founder_role():
    assert is_founder_role("Co-Founder & CEO") is True
    assert is_founder_role("CTO") is True
    assert is_founder_role("Marketing Manager") is False


def test_rank_orders_scraped_founder_first():
    contacts = [
        CandidateContact(email="hello@acme.com", found_via="generic", confidence=0.4),
        CandidateContact(email="jane@acme.com", role="Founder", found_via="scraped",
                         confidence=0.6, verified=True),
        CandidateContact(email="guess@acme.com", found_via="pattern_guess",
                         confidence=0.7, verified=True),
        CandidateContact(email="api@acme.com", role="CTO", found_via="api", confidence=0.9),
    ]
    ranked = rank_contacts(contacts)
    assert ranked[0].email == "jane@acme.com"     # scraped founder wins
    assert ranked[-1].email == "hello@acme.com"   # generic inbox last


def test_rank_dedupes_by_email_keeping_strongest():
    contacts = [
        CandidateContact(email="jane@acme.com", found_via="pattern_guess", confidence=0.5),
        CandidateContact(email="jane@acme.com", role="Founder", found_via="scraped",
                         confidence=0.6, verified=True),
    ]
    ranked = rank_contacts(contacts)
    assert len(ranked) == 1
    assert ranked[0].found_via == "scraped"
