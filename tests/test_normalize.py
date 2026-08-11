from app.scraper.sources.base import StartupRecord, normalize_domain


def test_normalize_domain_variants():
    assert normalize_domain("https://www.acme.com/about") == "acme.com"
    assert normalize_domain("http://acme.com") == "acme.com"
    assert normalize_domain("ACME.COM") == "acme.com"
    assert normalize_domain("www.acme.co.in/") == "acme.co.in"
    assert normalize_domain("acme.com") == "acme.com"
    assert normalize_domain("") is None
    assert normalize_domain(None) is None
    assert normalize_domain("not a domain") is None


def test_record_derives_domain():
    r = StartupRecord(name="Acme", website="https://www.acme.com/x")
    assert r.domain == "acme.com"


def test_record_explicit_domain_wins():
    r = StartupRecord(name="Acme", website="https://redirect.example.com", domain="acme.com")
    assert r.domain == "acme.com"
