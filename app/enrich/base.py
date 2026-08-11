from typing import Protocol

from app.scraper.email_finder import CandidateContact


class Enricher(Protocol):
    name: str

    def domain_search(self, domain: str) -> list[CandidateContact]: ...
