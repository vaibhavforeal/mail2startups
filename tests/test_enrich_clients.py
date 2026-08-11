import respx
from httpx import Response

from app.enrich.apollo import ApolloClient
from app.enrich.hunter import HunterClient

HUNTER_FIXTURE = {
    "data": {"emails": [
        {"value": "jane@acme.com", "first_name": "Jane", "last_name": "Roe",
         "position": "CEO", "confidence": 95},
        {"value": "hello@acme.com", "first_name": None, "last_name": None,
         "position": None, "confidence": 50},
    ]}
}

APOLLO_FIXTURE = {
    "people": [
        {"email": "raj@acme.com", "name": "Raj Kumar", "title": "CTO"},
    ]
}


@respx.mock
def test_hunter_domain_search_maps_fields():
    from app.enrich import hunter
    route = respx.get(hunter.HUNTER_URL).mock(return_value=Response(200, json=HUNTER_FIXTURE))
    contacts = HunterClient(api_key="k").domain_search("acme.com")
    assert route.called
    assert contacts[0].email == "jane@acme.com"
    assert contacts[0].name == "Jane Roe" and contacts[0].role == "CEO"
    assert contacts[0].found_via == "api"
    assert 0.9 < contacts[0].confidence <= 1.0
    assert contacts[1].found_via == "generic"  # generic inbox tagged


@respx.mock
def test_apollo_domain_search_maps_fields():
    from app.enrich import apollo
    route = respx.post(apollo.APOLLO_URL).mock(return_value=Response(200, json=APOLLO_FIXTURE))
    contacts = ApolloClient(api_key="k").domain_search("acme.com")
    assert route.called
    assert route.calls[0].request.headers["x-api-key"] == "k"
    assert contacts[0].email == "raj@acme.com"
    assert contacts[0].name == "Raj Kumar" and contacts[0].role == "CTO"
    assert contacts[0].found_via == "api"
