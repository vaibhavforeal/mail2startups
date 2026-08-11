import re
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlparse

_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


def normalize_domain(value: str | None) -> str | None:
    """Reduce a URL or bare domain to a lowercase registrable-ish domain string."""
    if not value:
        return None
    value = value.strip().lower()
    if "://" in value:
        value = urlparse(value).netloc
    else:
        value = value.split("/")[0]
    value = value.removeprefix("www.").rstrip(".")
    if not _DOMAIN_RE.match(value):
        return None
    return value


@dataclass
class StartupRecord:
    name: str
    website: str | None = None
    domain: str | None = None
    description: str = ""
    location: str = ""
    industry: str = ""
    team_size: int | None = None
    founder_names: list[str] = field(default_factory=list)
    contact_emails: list[str] = field(default_factory=list)
    source: str = ""

    def __post_init__(self) -> None:
        if self.domain is None:
            self.domain = normalize_domain(self.website)
        else:
            self.domain = normalize_domain(self.domain)


class Source(Protocol):
    name: str

    def fetch(self, limit: int = 100, **filters) -> list[StartupRecord]: ...
