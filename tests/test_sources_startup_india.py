import respx
from httpx import Response

from app.scraper.sources import get_source
from app.scraper.sources import startup_india

SEARCH_FIXTURE = {
    "content": [
        {"id": "p1", "name": "DesiTech", "country": "India", "state": "Karnataka", "city": "Bengaluru"},
        {"id": "p2", "name": "AgriNext", "country": "India", "state": "Maharashtra", "city": "Pune"},
    ],
    "totalPages": 1, "totalElements": 2, "number": 0,
}

PROFILE_P1 = {"user": {"startup": {"website": "https://desitech.in",
                                   "email": "founder@desitech.in",
                                   "sector": {"industryName": "FinTech"}}}}
PROFILE_P2 = {"user": {"startup": {}}}


@respx.mock
def test_startup_india_fetch(monkeypatch):
    monkeypatch.setattr(startup_india, "PROFILE_DELAY_SECONDS", 0)
    respx.post(startup_india.SEARCH_URL).mock(return_value=Response(200, json=SEARCH_FIXTURE))
    respx.get(startup_india.PROFILE_URL.format(profile_id="p1")).mock(
        return_value=Response(200, json=PROFILE_P1))
    respx.get(startup_india.PROFILE_URL.format(profile_id="p2")).mock(
        return_value=Response(200, json=PROFILE_P2))

    records = get_source("startup_india").fetch(limit=10)
    assert len(records) == 2
    desi = records[0]
    assert desi.name == "DesiTech"
    assert desi.location == "Bengaluru, Karnataka"
    assert desi.domain == "desitech.in"
    assert desi.contact_emails == ["founder@desitech.in"]
    assert desi.industry == "FinTech"
    # profile without website/email still yields a record
    assert records[1].domain is None


def test_find_first_key():
    data = {"a": {"b": [{"website": "https://x.io"}]}}
    assert startup_india._find_first(data, ("website",)) == "https://x.io"
    assert startup_india._find_first(data, ("missing",)) is None
