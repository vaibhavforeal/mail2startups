import httpx

from app.scraper.email_finder import CandidateContact, is_generic_email

APOLLO_URL = "https://api.apollo.io/v1/mixed_people/search"


class ApolloClient:
    name = "apollo"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def domain_search(self, domain: str) -> list[CandidateContact]:
        resp = httpx.post(
            APOLLO_URL,
            headers={"X-Api-Key": self.api_key, "Content-Type": "application/json"},
            json={"q_organization_domains": domain, "page": 1},
            timeout=30,
        )
        resp.raise_for_status()
        people = resp.json().get("people", [])
        out: list[CandidateContact] = []
        for person in people:
            email = (person.get("email") or "").lower()
            if not email:
                continue
            out.append(CandidateContact(
                email=email,
                name=person.get("name") or None,
                role=person.get("title") or "",
                found_via="generic" if is_generic_email(email) else "api",
                confidence=0.8,
                verified=True,
            ))
        return out
