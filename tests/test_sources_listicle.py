import respx
from httpx import Response

from app.ai import ExtractedStartup
from app.scraper.sources import get_source
from app.scraper.sources import listicle


HTML = "<html><body><h1>Top 2 startups</h1><p>1. Acme - acme.com</p><script>junk()</script></body></html>"


@respx.mock
def test_listicle_fetch(monkeypatch):
    respx.get("https://example.com/top-startups").mock(
        return_value=Response(200, text=HTML))

    def fake_extract(text, client=None):
        assert "Acme" in text
        assert "junk()" not in text  # scripts stripped
        return [ExtractedStartup(name="Acme", website="https://acme.com",
                                 description="widgets")]

    monkeypatch.setattr(listicle, "extract_startups_from_text", fake_extract)
    records = get_source("listicle").fetch(url="https://example.com/top-startups")
    assert len(records) == 1
    assert records[0].domain == "acme.com"
    assert records[0].source == "listicle"


def test_listicle_requires_url():
    import pytest
    with pytest.raises(ValueError):
        get_source("listicle").fetch()
