import pytest
import respx
from httpx import Response

from app.scraper.sources import get_source
from app.scraper.sources import product_hunt

PH_FIXTURE = {
    "data": {"posts": {"edges": [
        {"node": {"name": "LaunchPad", "tagline": "Ship faster",
                  "description": "A tool to ship faster.",
                  "website": "https://launchpad.dev",
                  "topics": {"edges": [{"node": {"name": "Developer Tools"}}]}}},
    ]}}
}


@respx.mock
def test_product_hunt_fetch(monkeypatch):
    monkeypatch.setenv("M2S_PRODUCT_HUNT_TOKEN", "test-token")
    route = respx.post(product_hunt.GRAPHQL_URL).mock(
        return_value=Response(200, json=PH_FIXTURE))
    records = get_source("product_hunt").fetch(limit=5, topic="developer-tools")
    assert route.called
    assert route.calls[0].request.headers["authorization"] == "Bearer test-token"
    assert len(records) == 1
    r = records[0]
    assert r.name == "LaunchPad"
    assert r.domain == "launchpad.dev"
    assert r.industry == "Developer Tools"
    assert r.source == "product_hunt"


def test_missing_token_raises(monkeypatch):
    monkeypatch.delenv("M2S_PRODUCT_HUNT_TOKEN", raising=False)
    with pytest.raises(ValueError, match="M2S_PRODUCT_HUNT_TOKEN"):
        get_source("product_hunt").fetch()
