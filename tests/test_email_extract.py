from app.scraper.email_finder import (
    CandidateContact,
    extract_emails,
    is_generic_email,
)


def test_is_generic_email():
    assert is_generic_email("careers@acme.com") is True
    assert is_generic_email("hello@acme.com") is True
    assert is_generic_email("jane.roe@acme.com") is False


def test_extract_plaintext_and_mailto():
    html = """
      <a href="mailto:Jane.Roe@Acme.com">email Jane</a>
      Reach the team at hello@acme.com or press@acme.com.
    """
    got = extract_emails(html)
    emails = [c.email for c in got]
    assert emails == ["jane.roe@acme.com", "hello@acme.com", "press@acme.com"]
    assert got[0].found_via == "scraped" and got[0].confidence == 0.6
    assert got[1].found_via == "generic" and got[1].confidence == 0.4


def test_extract_deobfuscated():
    text = "Contact jane [at] acme [dot] com or raj (at) acme (dot) com"
    emails = [c.email for c in extract_emails(text)]
    assert "jane@acme.com" in emails
    assert "raj@acme.com" in emails


def test_extract_dedupes_and_skips_assets():
    text = "a@acme.com A@ACME.COM logo@2x.png sprite@3x.gif"
    emails = [c.email for c in extract_emails(text)]
    assert emails == ["a@acme.com"]  # case-folded dupe merged, image assets dropped
