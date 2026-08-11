from app.scraper.email_finder import CandidateContact

FOUNDER_ROLE_KEYWORDS: tuple[str, ...] = (
    "founder", "cofounder", "co-founder", "ceo", "cto", "coo", "cfo",
    "owner", "president", "chief",
)

_SOURCE_RANK = {"scraped": 3, "api": 2, "pattern_guess": 1, "generic": 0}


def is_founder_role(role: str) -> bool:
    low = (role or "").lower()
    return any(keyword in low for keyword in FOUNDER_ROLE_KEYWORDS)


def _score(contact: CandidateContact) -> tuple:
    return (
        1 if is_founder_role(contact.role) else 0,
        _SOURCE_RANK.get(contact.found_via, 0),
        1 if contact.verified else 0,
        contact.confidence,
    )


def rank_contacts(contacts: list[CandidateContact]) -> list[CandidateContact]:
    best: dict[str, CandidateContact] = {}
    for contact in contacts:
        current = best.get(contact.email)
        if current is None or _score(contact) > _score(current):
            best[contact.email] = contact
    return sorted(best.values(), key=_score, reverse=True)
