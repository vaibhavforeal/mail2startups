from app.scraper.email_finder import guess_email_candidates


def test_guess_generates_ordered_patterns():
    cands = guess_email_candidates("Jane Roe", "acme.com", role="Founder")
    emails = [c.email for c in cands]
    assert emails[0] == "jane.roe@acme.com"      # highest-confidence first
    assert "jroe@acme.com" in emails
    assert "jane@acme.com" in emails
    assert all(c.found_via == "pattern_guess" for c in cands)
    assert all(c.name == "Jane Roe" and c.role == "Founder" for c in cands)
    assert cands[0].confidence >= cands[-1].confidence


def test_guess_handles_single_name():
    cands = guess_email_candidates("Cher", "acme.com")
    assert [c.email for c in cands] == ["cher@acme.com"]


def test_guess_requires_name_and_domain():
    assert guess_email_candidates("", "acme.com") == []
    assert guess_email_candidates("Jane Roe", "") == []


def test_guess_dedupes():
    # first==last edge cases must not yield duplicate addresses
    emails = [c.email for c in guess_email_candidates("Sam Sam", "acme.com")]
    assert len(emails) == len(set(emails))


def test_guess_dedupes_multipart_collision():
    cands = guess_email_candidates("Bob Ob", "acme.com")
    emails = [c.email for c in cands]
    assert len(emails) == len(set(emails))            # no duplicate addresses
    bob = next(c for c in cands if c.email == "bob@acme.com")
    assert bob.confidence == 0.6                       # highest-confidence variant kept
