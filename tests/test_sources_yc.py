import respx
from httpx import Response

from app.scraper.sources import get_source

YC_FIXTURE = [
    {
        "name": "Acme AI", "website": "https://www.acme.ai", "one_liner": "AI widgets",
        "long_description": "Acme builds AI widgets for enterprises.",
        "all_locations": "Bengaluru, India", "regions": ["India", "South Asia"],
        "industry": "B2B", "industries": ["B2B", "Engineering"],
        "team_size": 12, "status": "Active", "isHiring": True, "batch": "W25",
    },
    {
        "name": "Dead Co", "website": "https://dead.co", "one_liner": "gone",
        "long_description": "", "all_locations": "San Francisco, CA",
        "regions": ["America / Canada"], "industry": "B2B", "industries": [],
        "team_size": 0, "status": "Inactive", "isHiring": False, "batch": "S15",
    },
    {
        "name": "RemoteCo", "website": "https://remoteco.dev", "one_liner": "devtools",
        "long_description": "Remote-first devtools.", "all_locations": "Remote",
        "regions": ["Remote"], "industry": "Developer Tools", "industries": ["Developer Tools"],
        "team_size": 5, "status": "Active", "isHiring": True, "batch": "W26",
    },
]


@respx.mock
def test_yc_fetch_maps_fields():
    respx.get("https://yc-oss.github.io/api/companies/hiring.json").mock(
        return_value=Response(200, json=YC_FIXTURE)
    )
    records = get_source("yc").fetch(limit=10, list_name="hiring")
    assert [r.name for r in records] == ["Acme AI", "RemoteCo"]  # Inactive filtered out
    acme = records[0]
    assert acme.domain == "acme.ai"
    assert acme.team_size == 12
    assert acme.location == "Bengaluru, India"
    assert "AI widgets" in acme.description
    assert acme.source == "yc"


@respx.mock
def test_yc_region_filter():
    respx.get("https://yc-oss.github.io/api/companies/hiring.json").mock(
        return_value=Response(200, json=YC_FIXTURE)
    )
    src = get_source("yc")
    assert [r.name for r in src.fetch(list_name="hiring", region="india")] == ["Acme AI"]
    assert [r.name for r in src.fetch(list_name="hiring", region="remote")] == ["RemoteCo"]
