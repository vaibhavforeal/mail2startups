import re
from dataclasses import dataclass

GENERIC_LOCALPARTS: frozenset[str] = frozenset({
    "info", "hello", "hi", "contact", "contactus", "careers", "career", "jobs",
    "hr", "team", "support", "admin", "sales", "founders", "press", "media",
    "help", "office", "hey", "enquiry", "enquiries", "inquiries", "general",
})

# Image/asset local-parts or domains we never want to treat as contact emails.
_ASSET_RE = re.compile(r"\.(png|jpe?g|gif|svg|webp|css|js)$", re.I)

_EMAIL_RE = re.compile(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}", re.I)

# name [at] domain [dot] com  /  name (at) domain (dot) com  /  name at domain dot com
# Also supports multi-part TLDs: jane [at] acme [dot] co [dot] uk
_OBFUSCATED_RE = re.compile(
    r"([a-z0-9._%+\-]+)\s*[\[\(\{]?\s*(?:at|@)\s*[\]\)\}]?\s*"
    r"([a-z0-9.\-]+)\s*[\[\(\{]?\s*(?:dot|\.)\s*[\]\)\}]?\s*"
    r"([a-z]{2,}(?:\s*[\[\(\{]?\s*(?:dot|\.)\s*[\]\)\}]?\s*[a-z]{2,})*)",
    re.I,
)


@dataclass
class CandidateContact:
    email: str
    name: str | None = None
    role: str = ""
    found_via: str = "scraped"  # scraped|api|pattern_guess|generic
    confidence: float = 0.0
    verified: bool = False


def is_generic_email(email: str) -> bool:
    local = email.split("@", 1)[0].lower()
    return local in GENERIC_LOCALPARTS


def _clean(email: str) -> str | None:
    email = email.strip().strip(".").lower()
    if _ASSET_RE.search(email) or "@" not in email:
        return None
    return email


def extract_emails(text: str) -> list[CandidateContact]:
    found: list[str] = list(_EMAIL_RE.findall(text))
    for local, host, tld_raw in _OBFUSCATED_RE.findall(text):
        # Clean multi-part TLD: "co [dot] uk" or "co (dot) uk" -> "co.uk"
        tld = re.sub(r'\s*[\[\(\{]?\s*(?:dot|\.)\s*[\]\)\}]?\s*', '.', tld_raw)
        found.append(f"{local}@{host}.{tld}")

    out: list[CandidateContact] = []
    seen: set[str] = set()
    for raw in found:
        email = _clean(raw)
        if email is None or email in seen:
            continue
        seen.add(email)
        generic = is_generic_email(email)
        out.append(CandidateContact(
            email=email,
            found_via="generic" if generic else "scraped",
            confidence=0.4 if generic else 0.6,
        ))
    return out
