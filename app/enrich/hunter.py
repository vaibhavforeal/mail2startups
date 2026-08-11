import httpx

from app.scraper.email_finder import CandidateContact, is_generic_email

HUNTER_URL = "https://api.hunter.io/v2/domain-search"


class HunterClient:
    name = "hunter"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def domain_search(self, domain: str) -> list[CandidateContact]:
        resp = httpx.get(
            HUNTER_URL,
            params={"domain": domain, "api_key": self.api_key},
            timeout=30,
        )
        resp.raise_for_status()
        emails = resp.json().get("data", {}).get("emails", [])
        out: list[CandidateContact] = []
        for entry in emails:
            email = (entry.get("value") or "").lower()
            if not email:
                continue
            name = " ".join(p for p in [entry.get("first_name"), entry.get("last_name")] if p)
            out.append(CandidateContact(
                email=email,
                name=name or None,
                role=entry.get("position") or "",
                found_via="generic" if is_generic_email(email) else "api",
                confidence=(entry.get("confidence") or 0) / 100.0,
                verified=True,
            ))
        return out
