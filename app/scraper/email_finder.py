import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

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


@dataclass
class Person:
    name: str
    role: str


ROLE_KEYWORDS: tuple[str, ...] = (
    "co-founder", "cofounder", "co founder", "founder", "ceo", "cto", "coo",
    "cfo", "chief executive", "chief technology", "chief operating",
    "head of", "vp of", "vice president", "director", "owner", "president",
)

_NAME_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b")


def _match_role(line: str) -> str | None:
    low = line.lower()
    for keyword in ROLE_KEYWORDS:
        if keyword in low:
            return line.strip(" -–—:•|")
    return None


def _name_in(line: str) -> str | None:
    match = _NAME_RE.search(line)
    if not match:
        return None
    # Reject when the matched span is itself a role phrase (e.g. "Head Of").
    if _match_role(match.group(1)):
        return None
    return match.group(1)


def extract_people(html: str) -> list[Person]:
    soup = BeautifulSoup(html, "html.parser")
    lines = [line.strip() for line in soup.get_text("\n").split("\n") if line.strip()]

    people: list[Person] = []
    seen: set[str] = set()
    for i, line in enumerate(lines):
        role = _match_role(line)
        if not role:
            continue
        name = None
        for j in (i, i - 1, i + 1):
            if 0 <= j < len(lines):
                candidate = _name_in(lines[j])
                if candidate:
                    name = candidate
                    break
        if name and name not in seen:
            seen.add(name)
            people.append(Person(name=name, role=role))
    return people


_NON_ALNUM_RE = re.compile(r"[^a-z]")


def _name_parts(full_name: str) -> list[str]:
    parts = []
    for chunk in full_name.strip().lower().split():
        cleaned = _NON_ALNUM_RE.sub("", chunk)
        if cleaned:
            parts.append(cleaned)
    return parts


def guess_email_candidates(full_name: str, domain: str, role: str = "") -> list[CandidateContact]:
    if not domain:
        return []
    parts = _name_parts(full_name)
    if not parts:
        return []

    first = parts[0]
    last = parts[-1]
    patterns: list[tuple[str, float]] = []
    if first != last:
        f = first[0]
        patterns = [
            (f"{first}.{last}", 0.7),
            (f"{f}{last}", 0.6),
            (f"{first}", 0.5),
            (f"{first}{last}", 0.5),
            (f"{first}_{last}", 0.4),
            (f"{f}.{last}", 0.35),
            (f"{last}", 0.25),
        ]
    else:
        patterns = [(first, 0.5)]

    out: list[CandidateContact] = []
    best: dict[str, float] = {}
    for local, confidence in patterns:
        email = f"{local}@{domain}".lower()
        if email in best:
            continue
        best[email] = confidence
        out.append(CandidateContact(
            email=email,
            name=full_name,
            role=role,
            found_via="pattern_guess",
            confidence=confidence,
        ))
    return out
