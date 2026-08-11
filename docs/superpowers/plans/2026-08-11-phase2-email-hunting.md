# Mail2Startups Phase 2: Email Hunting & Enrichment — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** For every `discovered` startup, find the best founder/CTO (or fallback generic) contact email using a cheapest-method-first pipeline — site crawl → email/team extraction → founder-name gap-fill → pattern guessing → MX verification → free-tier API enrichment → ranking — persist the results as `contacts`, advance the startup to `enriched`, and expose it all as `m2s hunt`.

**Architecture:** A pure-function extraction/guessing core (`app/scraper/email_finder.py`) with zero I/O, an httpx-based per-domain crawler (`app/scraper/site_crawler.py`), an `app/enrich/` package for the outward-facing lookups (MX verification, exa founder search, Hunter/Apollo free-tier clients with DB-backed credit tracking, and contact ranking), and an orchestrator (`app/scraper/hunt.py`) that wires them together idempotently and writes to the schema built in Phase 1. A single `CandidateContact` dataclass flows through every stage so extraction, guessing, enrichment, and ranking all speak the same type. All network callers are dependency-injected so the entire suite runs offline.

**Tech Stack:** Python 3.12+, httpx + BeautifulSoup4 (crawl/parse), dnspython (MX lookups), SQLAlchemy 2.x (persistence + credit tracking), Typer (CLI), pytest + respx (offline tests). No new AI calls in this phase — Claude drafting is Phase 3.

## Global Constraints

- Python 3.12+; the dev machine is Windows 11 with Git Bash — run Python as `.venv/Scripts/python -m ...` (never `python3`), use forward slashes and `pathlib`, no OS-specific paths in code.
- Tests are fully offline: HTTP is mocked with `respx`; DNS resolvers, crawlers, search functions, and enrichment clients are dependency-injected and faked in tests. No live network calls in the test suite.
- Startup status values exactly as specced: `discovered, enriched, drafted, in_review, queued, sent, replied, bounced, no_response, dead`. Phase 2 only ever transitions `discovered → enriched`.
- `Contact.found_via` values exactly: `scraped | api | pattern_guess | generic` (defined Phase 1, reused verbatim here).
- All settings come from `app.config.Settings` (env prefix `M2S_`, loaded from `.env`); never hardcode keys. New keys added this phase: `M2S_EXA_API_KEY`, `M2S_HUNTER_API_KEY`, `M2S_APOLLO_API_KEY`, `M2S_HUNTER_MONTHLY_LIMIT`.
- External endpoint constants live at the top of each module so they can be updated in one place if an API changes.
- Enrichment is polite and cost-aware: crawler uses per-request timeouts and a browser User-Agent; paid-tier API calls are gated behind a monthly credit check and recorded in the DB before/after every call.
- Commit after every task; conventional-commit style messages; end each commit message with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Email extraction & de-obfuscation

**Files:**
- Create: `app/scraper/email_finder.py`
- Test: `tests/test_email_extract.py`

**Interfaces:**
- Consumes: nothing (pure functions)
- Produces:
  - `CandidateContact` dataclass — the type that flows through the whole hunt pipeline: `email: str`, `name: str | None = None`, `role: str = ""`, `found_via: str = "scraped"`, `confidence: float = 0.0`, `verified: bool = False`.
  - `GENERIC_LOCALPARTS: frozenset[str]` — local-parts treated as generic inboxes.
  - `is_generic_email(email: str) -> bool` — True when the local-part is a generic inbox (`hello@`, `careers@`, ...).
  - `extract_emails(text: str) -> list[CandidateContact]` — pulls plaintext + `mailto:` + obfuscated (`name [at] domain [dot] com`) emails from raw HTML or text; lower-cased, de-duplicated preserving first-seen order; `found_via` is `"generic"` for generic inboxes else `"scraped"`; `confidence` 0.6 for real named addresses, 0.4 for generic. Skips asset-looking addresses (e.g. `x@2x.png`).

- [ ] **Step 1: Write the failing test**

`tests/test_email_extract.py`:

```python
from app.scraper.email_finder import (
    CandidateContact,
    extract_emails,
    is_generic_email,
)


def test_is_generic_email():
    assert is_generic_email("careers@acme.com") is True
    assert is_generic_email("hello@acme.com") is True
    assert is_generic_email("jane.roe@acme.com") is False


def test_extract_plaintext_and_mailto():
    html = """
      <a href="mailto:Jane.Roe@Acme.com">email Jane</a>
      Reach the team at hello@acme.com or press@acme.com.
    """
    got = extract_emails(html)
    emails = [c.email for c in got]
    assert emails == ["jane.roe@acme.com", "hello@acme.com", "press@acme.com"]
    assert got[0].found_via == "scraped" and got[0].confidence == 0.6
    assert got[1].found_via == "generic" and got[1].confidence == 0.4


def test_extract_deobfuscated():
    text = "Contact jane [at] acme [dot] com or raj (at) acme (dot) com"
    emails = [c.email for c in extract_emails(text)]
    assert "jane@acme.com" in emails
    assert "raj@acme.com" in emails


def test_extract_dedupes_and_skips_assets():
    text = "a@acme.com A@ACME.COM logo@2x.png sprite@3x.gif"
    emails = [c.email for c in extract_emails(text)]
    assert emails == ["a@acme.com"]  # case-folded dupe merged, image assets dropped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_email_extract.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.scraper.email_finder'`

- [ ] **Step 3: Write minimal implementation**

`app/scraper/email_finder.py`:

```python
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
_OBFUSCATED_RE = re.compile(
    r"([a-z0-9._%+\-]+)\s*[\[\(\{]?\s*(?:at|@)\s*[\]\)\}]?\s*"
    r"([a-z0-9.\-]+)\s*[\[\(\{]?\s*(?:dot|\.)\s*[\]\)\}]?\s*([a-z]{2,})",
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
    for local, host, tld in _OBFUSCATED_RE.findall(text):
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_email_extract.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/scraper/email_finder.py tests/test_email_extract.py
git commit -m "feat: email extraction with mailto + de-obfuscation"
```

---

### Task 2: Team-page people extraction (names + roles)

**Files:**
- Modify: `app/scraper/email_finder.py`
- Test: `tests/test_people_extract.py`

**Interfaces:**
- Consumes: BeautifulSoup4
- Produces:
  - `Person` dataclass: `name: str`, `role: str`.
  - `ROLE_KEYWORDS: tuple[str, ...]` — lower-cased role phrases that mark a person of interest (founder/CEO/CTO/etc.).
  - `extract_people(html: str) -> list[Person]` — parses team/about HTML, pairs each role line with the nearest name (same line, else previous, else next), returns founders/execs first, de-duplicated by name. Non-people text (company names, taglines) is ignored because it carries no role keyword.

- [ ] **Step 1: Write the failing test**

`tests/test_people_extract.py`:

```python
from app.scraper.email_finder import Person, extract_people

TEAM_HTML = """
<div class="team">
  <div class="member"><h3>Jane Roe</h3><p>Co-Founder &amp; CEO</p></div>
  <div class="member"><h3>Raj Kumar</h3><p>CTO</p></div>
  <div class="member"><h3>Widget Corp</h3><p>Making widgets since 2020</p></div>
  <div class="member"><h3>Priya Nair — Head of Engineering</h3></div>
</div>
"""


def test_extract_people_pairs_name_and_role():
    people = extract_people(TEAM_HTML)
    names = [p.name for p in people]
    assert "Jane Roe" in names
    assert "Raj Kumar" in names
    assert "Priya Nair" in names          # same-line "Name — Role"
    assert "Widget Corp" not in names     # no role keyword -> not a person


def test_extract_people_captures_role():
    people = {p.name: p.role for p in extract_people(TEAM_HTML)}
    assert "founder" in people["Jane Roe"].lower()
    assert people["Raj Kumar"].lower() == "cto"


def test_extract_people_empty_when_no_roles():
    assert extract_people("<p>We build great software for everyone.</p>") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_people_extract.py -v`
Expected: FAIL with `ImportError: cannot import name 'Person'`

- [ ] **Step 3: Write minimal implementation**

Append to `app/scraper/email_finder.py` (add `from bs4 import BeautifulSoup` to the imports at the top of the file):

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_people_extract.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/scraper/email_finder.py tests/test_people_extract.py
git commit -m "feat: team-page people extraction (names + roles)"
```

---

### Task 3: Email pattern guessing

**Files:**
- Modify: `app/scraper/email_finder.py`
- Test: `tests/test_pattern_guess.py`

**Interfaces:**
- Consumes: `CandidateContact`
- Produces:
  - `guess_email_candidates(full_name: str, domain: str, role: str = "") -> list[CandidateContact]` — from a person's name + domain, generates ordered pattern candidates (`first.last@`, `flast@`, `first@`, `firstlast@`, `first_last@`, `f.last@`, `last@`) each as a `CandidateContact(found_via="pattern_guess", ...)` carrying the person's `name`/`role` and a per-pattern `confidence`. Returns `[]` when the name has no usable parts or `domain` is falsy. De-duplicates identical addresses keeping the highest confidence.

- [ ] **Step 1: Write the failing test**

`tests/test_pattern_guess.py`:

```python
from app.scraper.email_finder import guess_email_candidates


def test_guess_generates_ordered_patterns():
    cands = guess_email_candidates("Jane Roe", "acme.com", role="Founder")
    emails = [c.email for c in cands]
    assert emails[0] == "jane.roe@acme.com"      # highest-confidence first
    assert "jroe@acme.com" in emails
    assert "jane@acme.com" in emails
    assert all(c.found_via == "pattern_guess" for c in cands)
    assert all(c.name == "Jane Roe" and c.role == "Founder" for c in cands)
    assert cands[0].confidence >= cands[-1].confidence


def test_guess_handles_single_name():
    cands = guess_email_candidates("Cher", "acme.com")
    assert [c.email for c in cands] == ["cher@acme.com"]


def test_guess_requires_name_and_domain():
    assert guess_email_candidates("", "acme.com") == []
    assert guess_email_candidates("Jane Roe", "") == []


def test_guess_dedupes():
    # first==last edge cases must not yield duplicate addresses
    emails = [c.email for c in guess_email_candidates("Sam Sam", "acme.com")]
    assert len(emails) == len(set(emails))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_pattern_guess.py -v`
Expected: FAIL with `ImportError: cannot import name 'guess_email_candidates'`

- [ ] **Step 3: Write minimal implementation**

Append to `app/scraper/email_finder.py`:

```python
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
        if best.get(email, -1.0) >= confidence:
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_pattern_guess.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/scraper/email_finder.py tests/test_pattern_guess.py
git commit -m "feat: email pattern guessing from name + domain"
```

---

### Task 4: MX verification

**Files:**
- Modify: `pyproject.toml` (add `dnspython`)
- Create: `app/enrich/__init__.py` (empty)
- Create: `app/enrich/verify.py`
- Test: `tests/test_verify.py`

**Interfaces:**
- Consumes: `CandidateContact`, dnspython (`dns.resolver`)
- Produces:
  - `has_mx(domain: str, resolver=None) -> bool` — True when the domain publishes MX (or A as a fallback) records; `resolver` is injectable (defaults to a module `dns.resolver.Resolver()`); any DNS exception → False.
  - `verify_candidates(candidates: list[CandidateContact], resolver=None) -> list[CandidateContact]` — sets `verified=True` on candidates whose domain has MX (caches lookups per domain), leaving others untouched; returns the same list objects mutated. Deliverability of the *domain*, not proof of the exact mailbox.

- [ ] **Step 1: Add dnspython to pyproject.toml dependencies**

In `pyproject.toml`, add to the `[project].dependencies` list:

```toml
    "dnspython>=2.6",
```

Then install: `.venv/Scripts/python -m pip install -e ".[dev]"`

- [ ] **Step 2: Write the failing test**

`tests/test_verify.py`:

```python
import dns.resolver

from app.enrich.verify import has_mx, verify_candidates
from app.scraper.email_finder import CandidateContact


class FakeResolver:
    def __init__(self, good_domains):
        self.good = set(good_domains)
        self.calls = []

    def resolve(self, domain, rdtype):
        self.calls.append((domain, rdtype))
        if rdtype == "MX" and domain in self.good:
            return ["10 mail.%s." % domain]
        raise dns.resolver.NXDOMAIN()


def test_has_mx_true_and_false():
    resolver = FakeResolver(good_domains={"acme.com"})
    assert has_mx("acme.com", resolver=resolver) is True
    assert has_mx("nope.invalid", resolver=resolver) is False


def test_verify_candidates_sets_flag_and_caches():
    resolver = FakeResolver(good_domains={"acme.com"})
    cands = [
        CandidateContact(email="jane@acme.com", found_via="pattern_guess", confidence=0.7),
        CandidateContact(email="raj@acme.com", found_via="pattern_guess", confidence=0.6),
        CandidateContact(email="x@ghost.invalid", found_via="pattern_guess", confidence=0.5),
    ]
    out = verify_candidates(cands, resolver=resolver)
    assert out[0].verified is True and out[1].verified is True
    assert out[2].verified is False
    # acme.com resolved once despite two candidates (per-domain cache)
    assert [d for d, _ in resolver.calls].count("acme.com") == 1
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_verify.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.enrich'`

- [ ] **Step 4: Write minimal implementation**

Create empty `app/enrich/__init__.py`, then `app/enrich/verify.py`:

```python
import dns.resolver

from app.scraper.email_finder import CandidateContact

_DEFAULT_RESOLVER = dns.resolver.Resolver()
_DEFAULT_RESOLVER.lifetime = 5.0
_DEFAULT_RESOLVER.timeout = 5.0


def has_mx(domain: str, resolver=None) -> bool:
    resolver = resolver or _DEFAULT_RESOLVER
    if not domain:
        return False
    try:
        answers = resolver.resolve(domain, "MX")
        return len(list(answers)) > 0
    except Exception:
        return False


def verify_candidates(candidates: list[CandidateContact], resolver=None) -> list[CandidateContact]:
    cache: dict[str, bool] = {}
    for candidate in candidates:
        domain = candidate.email.split("@", 1)[-1]
        if domain not in cache:
            cache[domain] = has_mx(domain, resolver=resolver)
        if cache[domain]:
            candidate.verified = True
    return candidates
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_verify.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml app/enrich/__init__.py app/enrich/verify.py tests/test_verify.py
git commit -m "feat: MX-based domain verification for contacts"
```

---

### Task 5: Site crawler

**Files:**
- Create: `app/scraper/site_crawler.py`
- Test: `tests/test_site_crawler.py`

**Interfaces:**
- Consumes: httpx
- Produces:
  - `CrawledPage` dataclass: `url: str`, `html: str`, `status: int`.
  - `DEFAULT_PATHS: tuple[str, ...]` — the relative paths tried per domain (`/`, `/about`, `/about-us`, `/team`, `/contact`, `/careers`, `/company`).
  - `crawl_site(domain: str, client=None, paths=DEFAULT_PATHS) -> list[CrawledPage]` — GETs `https://{domain}{path}` for each path with a browser UA, per-request timeout, redirects followed; collects only `200` responses whose body looks like HTML; swallows per-path connection/timeout errors so one bad path never aborts the crawl. `client` is an injectable `httpx.Client` for tests.

- [ ] **Step 1: Write the failing test**

`tests/test_site_crawler.py`:

```python
import httpx
import respx
from httpx import Response

from app.scraper.site_crawler import CrawledPage, crawl_site


@respx.mock
def test_crawl_collects_200_html_pages():
    respx.get("https://acme.com/").mock(return_value=Response(200, html="<h1>Home</h1>"))
    respx.get("https://acme.com/about").mock(return_value=Response(200, html="<h1>About</h1>"))
    respx.get("https://acme.com/team").mock(return_value=Response(404, text="nope"))
    # all other default paths -> 404
    for path in ("/about-us", "/contact", "/careers", "/company"):
        respx.get(f"https://acme.com{path}").mock(return_value=Response(404))

    with httpx.Client() as client:
        pages = crawl_site("acme.com", client=client)

    urls = [p.url for p in pages]
    assert "https://acme.com/" in urls
    assert "https://acme.com/about" in urls
    assert "https://acme.com/team" not in urls  # 404 skipped
    assert all(isinstance(p, CrawledPage) and p.status == 200 for p in pages)


@respx.mock
def test_crawl_swallows_connection_errors():
    respx.get("https://acme.com/").mock(side_effect=httpx.ConnectError("boom"))
    for path in ("/about", "/about-us", "/team", "/contact", "/careers", "/company"):
        respx.get(f"https://acme.com{path}").mock(return_value=Response(200, html="<p>ok</p>"))
    with httpx.Client() as client:
        pages = crawl_site("acme.com", client=client)
    assert len(pages) == 6  # the erroring root did not abort the rest
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_site_crawler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.scraper.site_crawler'`

- [ ] **Step 3: Write minimal implementation**

`app/scraper/site_crawler.py`:

```python
from contextlib import nullcontext
from dataclasses import dataclass

import httpx

DEFAULT_PATHS: tuple[str, ...] = (
    "/", "/about", "/about-us", "/team", "/contact", "/careers", "/company",
)

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

REQUEST_TIMEOUT = 15.0


@dataclass
class CrawledPage:
    url: str
    html: str
    status: int


def _looks_like_html(response: httpx.Response) -> bool:
    ctype = response.headers.get("content-type", "")
    return "html" in ctype.lower() or "<" in response.text[:200]


def crawl_site(domain: str, client: httpx.Client | None = None,
               paths: tuple[str, ...] = DEFAULT_PATHS) -> list[CrawledPage]:
    owns_client = client is None
    ctx = httpx.Client(headers={"User-Agent": BROWSER_UA},
                       timeout=REQUEST_TIMEOUT, follow_redirects=True) if owns_client \
        else nullcontext(client)

    pages: list[CrawledPage] = []
    with ctx as active:
        for path in paths:
            url = f"https://{domain}{path}"
            try:
                response = active.get(url)
            except httpx.HTTPError:
                continue
            if response.status_code == 200 and _looks_like_html(response):
                pages.append(CrawledPage(url=url, html=response.text, status=200))
    return pages
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_site_crawler.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/scraper/site_crawler.py tests/test_site_crawler.py
git commit -m "feat: per-domain site crawler with fault-tolerant paths"
```

---

### Task 6: Founder-name gap-fill via exa search

**Files:**
- Modify: `app/config.py` (add `exa_api_key`)
- Modify: `.env.example` (document `M2S_EXA_API_KEY`)
- Create: `app/enrich/founder_search.py`
- Test: `tests/test_founder_search.py`

**Interfaces:**
- Consumes: httpx, `app.config.get_settings`
- Produces:
  - `SEARCH_URL: str` — the exa REST endpoint constant.
  - `find_founder_names(startup_name: str, search_fn=None, limit: int = 5) -> list[str]` — runs a web search for the startup's founders and returns de-duplicated human names (no LinkedIn scraping — names only, from result titles/snippets). `search_fn(query: str) -> list[dict]` is injectable (each dict has `title` and/or `text`); the default calls exa via httpx using `M2S_EXA_API_KEY`. Returns `[]` when the key is missing and no `search_fn` is injected.
  - `_extract_names_from_snippets(snippets: list[str]) -> list[str]` — helper pulling capitalized full-name spans.

- [ ] **Step 1: Add config + env key**

In `app/config.py`, add a field to `Settings`:

```python
    exa_api_key: str = ""
```

In `.env.example`, add:

```bash
# exa.ai search API (founder-name gap-fill)
M2S_EXA_API_KEY=
```

- [ ] **Step 2: Write the failing test**

`tests/test_founder_search.py`:

```python
from app.enrich.founder_search import (
    _extract_names_from_snippets,
    find_founder_names,
)


def test_extract_names_from_snippets():
    names = _extract_names_from_snippets([
        "Jane Roe is the co-founder and CEO of Acme.",
        "Acme was founded by Raj Kumar and Jane Roe.",
        "Our mission is to build widgets.",  # no names
    ])
    assert "Jane Roe" in names
    assert "Raj Kumar" in names
    assert names.count("Jane Roe") == 1  # de-duplicated


def test_find_founder_names_uses_injected_search():
    def fake_search(query):
        assert "Acme" in query
        return [
            {"title": "Jane Roe - Founder, Acme", "text": "Jane Roe leads Acme."},
            {"title": "About", "text": "Co-founded by Raj Kumar."},
        ]

    names = find_founder_names("Acme", search_fn=fake_search)
    assert set(names) >= {"Jane Roe", "Raj Kumar"}


def test_find_founder_names_no_key_no_fn_returns_empty(monkeypatch):
    monkeypatch.delenv("M2S_EXA_API_KEY", raising=False)
    assert find_founder_names("Acme") == []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_founder_search.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.enrich.founder_search'`

- [ ] **Step 4: Write minimal implementation**

`app/enrich/founder_search.py`:

```python
import re

import httpx

from app.config import get_settings

SEARCH_URL = "https://api.exa.ai/search"

_NAME_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b")

# Capitalized spans that are never a person's name.
_NAME_STOPWORDS = frozenset({
    "The", "Our", "About", "Founder", "Founders", "Co", "Chief", "Team",
    "Acme", "Inc", "Ltd", "Llc", "Startup", "India", "Ceo", "Cto",
})


def _extract_names_from_snippets(snippets: list[str]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for snippet in snippets:
        for match in _NAME_RE.findall(snippet):
            first_word = match.split()[0]
            if first_word in _NAME_STOPWORDS:
                continue
            if match not in seen:
                seen.add(match)
                names.append(match)
    return names


def _exa_search(query: str) -> list[dict]:
    key = get_settings().exa_api_key
    if not key:
        return []
    resp = httpx.post(
        SEARCH_URL,
        headers={"x-api-key": key, "Content-Type": "application/json"},
        json={"query": query, "numResults": 5, "contents": {"text": True}},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


def find_founder_names(startup_name: str, search_fn=None, limit: int = 5) -> list[str]:
    search_fn = search_fn or _exa_search
    query = f"{startup_name} startup founder CEO CTO"
    results = search_fn(query)
    snippets = [
        f"{item.get('title', '')} {item.get('text', '')}".strip()
        for item in results
    ]
    return _extract_names_from_snippets(snippets)[:limit]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_founder_search.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add app/config.py .env.example app/enrich/founder_search.py tests/test_founder_search.py
git commit -m "feat: founder-name gap-fill via exa web search"
```

---

### Task 7: Free-tier enrichment — credit tracking + Hunter/Apollo clients

**Files:**
- Modify: `app/models.py` (add `EnrichmentUsage`)
- Modify: `app/config.py` (add `hunter_api_key`, `apollo_api_key`, `hunter_monthly_limit`)
- Modify: `.env.example`
- Create: `app/enrich/base.py`
- Create: `app/enrich/usage.py`
- Create: `app/enrich/hunter.py`
- Create: `app/enrich/apollo.py`
- Test: `tests/test_enrich_usage.py`
- Test: `tests/test_enrich_clients.py`

**Interfaces:**
- Consumes: `CandidateContact`, httpx, SQLAlchemy `Session`, `app.config.get_settings`
- Produces:
  - `app.models.EnrichmentUsage` ORM: `id`, `provider: str`, `period: str` (`YYYY-MM`), `calls: int`, unique on `(provider, period)`.
  - `app.enrich.base.Enricher` Protocol: attribute `name: str`; method `domain_search(self, domain: str) -> list[CandidateContact]`.
  - `app.enrich.usage.current_period(now=None) -> str`; `usage_this_month(session, provider, period=None) -> int`; `record_call(session, provider, period=None) -> int` (upsert-increment, returns new count); `remaining(session, provider, limit, period=None) -> int`.
  - `app.enrich.hunter.HunterClient` — `name="hunter"`, `HunterClient(api_key: str)`, `domain_search(domain) -> list[CandidateContact]` (found_via `"api"`, confidence from Hunter's 0–100 score scaled to 0–1, generic addresses tagged found_via `"generic"`). Endpoint constant `HUNTER_URL`.
  - `app.enrich.apollo.ApolloClient` — `name="apollo"`, `ApolloClient(api_key: str)`, `domain_search(domain) -> list[CandidateContact]` via `X-Api-Key` header. Endpoint constant `APOLLO_URL`.

- [ ] **Step 1: Add the EnrichmentUsage model**

Append to `app/models.py`:

```python
from sqlalchemy import UniqueConstraint


class EnrichmentUsage(Base):
    __tablename__ = "enrichment_usage"
    __table_args__ = (UniqueConstraint("provider", "period", name="uq_provider_period"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(50))
    period: Mapped[str] = mapped_column(String(7))  # YYYY-MM
    calls: Mapped[int] = mapped_column(Integer, default=0)
```

(Add `UniqueConstraint` to the existing `from sqlalchemy import ...` line instead of a second import if you prefer; a separate import line is fine.)

- [ ] **Step 2: Add config keys + env docs**

In `app/config.py`, add to `Settings`:

```python
    hunter_api_key: str = ""
    apollo_api_key: str = ""
    hunter_monthly_limit: int = 25
```

In `.env.example`, add:

```bash
# Free-tier enrichment providers
M2S_HUNTER_API_KEY=
M2S_APOLLO_API_KEY=
M2S_HUNTER_MONTHLY_LIMIT=25
```

- [ ] **Step 3: Write the failing tests**

`tests/test_enrich_usage.py`:

```python
from datetime import datetime, timezone

from app.enrich.usage import current_period, record_call, remaining, usage_this_month


def test_current_period_format():
    fixed = datetime(2026, 8, 11, tzinfo=timezone.utc)
    assert current_period(now=fixed) == "2026-08"


def test_record_and_read_usage(session):
    period = "2026-08"
    assert usage_this_month(session, "hunter", period=period) == 0
    assert record_call(session, "hunter", period=period) == 1
    assert record_call(session, "hunter", period=period) == 2
    assert usage_this_month(session, "hunter", period=period) == 2
    assert remaining(session, "hunter", limit=25, period=period) == 23
    # other provider is independent
    assert usage_this_month(session, "apollo", period=period) == 0
```

`tests/test_enrich_clients.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_enrich_usage.py tests/test_enrich_clients.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.enrich.usage'`

- [ ] **Step 5: Write minimal implementation**

`app/enrich/base.py`:

```python
from typing import Protocol

from app.scraper.email_finder import CandidateContact


class Enricher(Protocol):
    name: str

    def domain_search(self, domain: str) -> list[CandidateContact]: ...
```

`app/enrich/usage.py`:

```python
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import EnrichmentUsage


def current_period(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m")


def _row(session: Session, provider: str, period: str) -> EnrichmentUsage | None:
    return session.scalars(
        select(EnrichmentUsage).where(
            EnrichmentUsage.provider == provider,
            EnrichmentUsage.period == period,
        )
    ).first()


def usage_this_month(session: Session, provider: str, period: str | None = None) -> int:
    period = period or current_period()
    row = _row(session, provider, period)
    return row.calls if row else 0


def record_call(session: Session, provider: str, period: str | None = None) -> int:
    period = period or current_period()
    row = _row(session, provider, period)
    if row is None:
        row = EnrichmentUsage(provider=provider, period=period, calls=0)
        session.add(row)
    row.calls += 1
    session.commit()
    return row.calls


def remaining(session: Session, provider: str, limit: int, period: str | None = None) -> int:
    return max(0, limit - usage_this_month(session, provider, period=period))
```

`app/enrich/hunter.py`:

```python
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
```

`app/enrich/apollo.py`:

```python
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
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_enrich_usage.py tests/test_enrich_clients.py -v`
Expected: PASS (4 tests)

- [ ] **Step 7: Commit**

```bash
git add app/models.py app/config.py .env.example app/enrich/base.py app/enrich/usage.py app/enrich/hunter.py app/enrich/apollo.py tests/test_enrich_usage.py tests/test_enrich_clients.py
git commit -m "feat: free-tier enrichment clients with DB credit tracking"
```

---

### Task 8: Contact ranking

**Files:**
- Create: `app/enrich/ranking.py`
- Test: `tests/test_ranking.py`

**Interfaces:**
- Consumes: `CandidateContact`
- Produces:
  - `FOUNDER_ROLE_KEYWORDS: tuple[str, ...]` — role substrings that mark a decision-maker.
  - `is_founder_role(role: str) -> bool`.
  - `rank_contacts(contacts: list[CandidateContact]) -> list[CandidateContact]` — de-duplicates by email (keeping the strongest instance), then sorts best-first by: founder-role, then source rank (`scraped` > `api` > `pattern_guess` > `generic`), then `verified`, then `confidence`. Returns a new list; input is not mutated.

- [ ] **Step 1: Write the failing test**

`tests/test_ranking.py`:

```python
from app.enrich.ranking import is_founder_role, rank_contacts
from app.scraper.email_finder import CandidateContact


def test_is_founder_role():
    assert is_founder_role("Co-Founder & CEO") is True
    assert is_founder_role("CTO") is True
    assert is_founder_role("Marketing Manager") is False


def test_rank_orders_scraped_founder_first():
    contacts = [
        CandidateContact(email="hello@acme.com", found_via="generic", confidence=0.4),
        CandidateContact(email="jane@acme.com", role="Founder", found_via="scraped",
                         confidence=0.6, verified=True),
        CandidateContact(email="guess@acme.com", found_via="pattern_guess",
                         confidence=0.7, verified=True),
        CandidateContact(email="api@acme.com", role="CTO", found_via="api", confidence=0.9),
    ]
    ranked = rank_contacts(contacts)
    assert ranked[0].email == "jane@acme.com"     # scraped founder wins
    assert ranked[-1].email == "hello@acme.com"   # generic inbox last


def test_rank_dedupes_by_email_keeping_strongest():
    contacts = [
        CandidateContact(email="jane@acme.com", found_via="pattern_guess", confidence=0.5),
        CandidateContact(email="jane@acme.com", role="Founder", found_via="scraped",
                         confidence=0.6, verified=True),
    ]
    ranked = rank_contacts(contacts)
    assert len(ranked) == 1
    assert ranked[0].found_via == "scraped"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_ranking.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.enrich.ranking'`

- [ ] **Step 3: Write minimal implementation**

`app/enrich/ranking.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_ranking.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/enrich/ranking.py tests/test_ranking.py
git commit -m "feat: contact ranking (scraped founder > api > guess > generic)"
```

---

### Task 9: Hunt orchestration

**Files:**
- Create: `app/scraper/hunt.py`
- Test: `tests/test_hunt.py`

**Interfaces:**
- Consumes: everything above — `crawl_site`, `extract_emails`, `extract_people`, `guess_email_candidates`, `find_founder_names`, `verify_candidates`, `rank_contacts`, enrichment clients + `usage`, ORM models, a `Session`.
- Produces:
  - `HuntResult` dataclass: `startup_id: int`, `contacts_added: int`, `enriched: bool`.
  - `hunt_startup(session, startup, *, crawler=crawl_site, founder_search=find_founder_names, enricher=None, resolver=None, monthly_limit=25) -> HuntResult`. Pipeline, cheapest first: crawl the domain → extract scraped emails + team people → if no founder names known, gap-fill via `founder_search` → pattern-guess candidates for each named person → MX-verify all candidates → if still nothing usable and an `enricher` is supplied with remaining monthly credits, call it (recording the call in `enrichment_usage`) → rank → persist as `Contact` rows (deduped by `(startup_id, email)`) → set `startup.status = ENRICHED` when ≥1 contact was stored, log `Event(kind="enriched")`; when 0 contacts found, leave status `discovered` and log `Event(kind="scrape_failed")`. Startups with no domain are a no-op `scrape_failed`.
  - `hunt_all(session, *, limit=50, enricher=None, resolver=None, monthly_limit=25) -> list[HuntResult]` — runs `hunt_startup` over startups currently in `discovered` status that have a domain, up to `limit`.

- [ ] **Step 1: Write the failing test**

`tests/test_hunt.py`:

```python
from sqlalchemy import select

from app.models import Contact, Event, Startup, StartupStatus
from app.scraper.email_finder import CandidateContact
from app.scraper.hunt import hunt_all, hunt_startup
from app.scraper.site_crawler import CrawledPage


def _make_startup(session, **kw):
    startup = Startup(name=kw.get("name", "Acme"), domain=kw.get("domain", "acme.com"),
                      source="yc")
    session.add(startup)
    session.commit()
    return startup


class FakeResolver:
    def resolve(self, domain, rdtype):
        return ["10 mail." + domain]  # every domain "has MX"


def test_hunt_startup_scrapes_and_enriches(session):
    startup = _make_startup(session)

    def fake_crawler(domain, client=None, paths=None):
        return [CrawledPage(
            url="https://acme.com/team",
            html="<h3>Jane Roe</h3><p>Founder &amp; CEO</p> Email jane@acme.com",
            status=200,
        )]

    result = hunt_startup(session, startup, crawler=fake_crawler,
                          founder_search=lambda name, **kw: [], resolver=FakeResolver())

    assert result.enriched is True and result.contacts_added >= 1
    session.refresh(startup)
    assert startup.status == StartupStatus.ENRICHED
    emails = {c.email for c in session.scalars(select(Contact)).all()}
    assert "jane@acme.com" in emails
    assert session.scalars(select(Event).where(Event.kind == "enriched")).first()


def test_hunt_gap_fills_founder_then_guesses(session):
    startup = _make_startup(session, name="StealthCo", domain="stealth.io")

    # No emails on the site, but a team page mentions nobody; founder search fills the gap.
    def fake_crawler(domain, client=None, paths=None):
        return [CrawledPage(url="https://stealth.io/", html="<p>Coming soon</p>", status=200)]

    result = hunt_startup(
        session, startup,
        crawler=fake_crawler,
        founder_search=lambda name, **kw: ["Priya Nair"],
        resolver=FakeResolver(),
    )
    assert result.contacts_added >= 1
    emails = {c.email for c in session.scalars(select(Contact)).all()}
    assert "priya.nair@stealth.io" in emails  # top pattern guess, MX-verified


def test_hunt_uses_enricher_only_as_last_resort(session):
    startup = _make_startup(session, name="Ghost", domain="ghost.io")
    calls = {"n": 0}

    class FakeEnricher:
        name = "hunter"
        def domain_search(self, domain):
            calls["n"] += 1
            return [CandidateContact(email="found@ghost.io", role="CEO",
                                     found_via="api", confidence=0.9, verified=True)]

    result = hunt_startup(
        session, startup,
        crawler=lambda domain, client=None, paths=None: [],   # nothing scraped
        founder_search=lambda name, **kw: [],                  # no founder names
        enricher=FakeEnricher(),
        resolver=FakeResolver(),
    )
    assert calls["n"] == 1  # enricher consulted because scraping + guessing found nothing
    assert result.contacts_added == 1
    assert result.enriched is True


def test_hunt_marks_scrape_failed_when_nothing_found(session):
    startup = _make_startup(session, name="Empty", domain="empty.io")
    result = hunt_startup(
        session, startup,
        crawler=lambda domain, client=None, paths=None: [],
        founder_search=lambda name, **kw: [],
        resolver=FakeResolver(),
    )
    assert result.enriched is False and result.contacts_added == 0
    session.refresh(startup)
    assert startup.status == StartupStatus.DISCOVERED
    assert session.scalars(select(Event).where(Event.kind == "scrape_failed")).first()


def test_hunt_all_processes_only_discovered(session):
    _make_startup(session, name="A", domain="a.io")
    done = _make_startup(session, name="B", domain="b.io")
    done.status = StartupStatus.ENRICHED
    session.commit()

    results = hunt_all(
        session, limit=10,
        # inject via module-level defaults override by monkeypatch in real run;
        # here we rely on hunt_all calling hunt_startup which we exercise with real crawler.
    )
    # Only the discovered startup 'A' is eligible; 'B' already enriched is skipped.
    assert {r.startup_id for r in results} == {session.scalars(
        select(Startup.id).where(Startup.name == "A")).one()}
```

Note: `test_hunt_all_processes_only_discovered` calls the real `crawl_site` against `a.io`, which will make a live request. To keep the suite offline, `hunt_all` must accept an injectable `crawler`/`founder_search`/`resolver` and pass them through — update the test to inject fakes:

```python
def test_hunt_all_processes_only_discovered(session):
    _make_startup(session, name="A", domain="a.io")
    done = _make_startup(session, name="B", domain="b.io")
    done.status = StartupStatus.ENRICHED
    session.commit()

    results = hunt_all(
        session, limit=10,
        crawler=lambda domain, client=None, paths=None: [],
        founder_search=lambda name, **kw: [],
        resolver=FakeResolver(),
    )
    assert len(results) == 1
    a_id = session.scalars(select(Startup.id).where(Startup.name == "A")).one()
    assert results[0].startup_id == a_id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_hunt.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.scraper.hunt'`

- [ ] **Step 3: Write minimal implementation**

`app/scraper/hunt.py`:

```python
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enrich import usage
from app.enrich.founder_search import find_founder_names
from app.enrich.ranking import rank_contacts
from app.enrich.verify import verify_candidates
from app.models import Contact, Event, Startup, StartupStatus
from app.scraper.email_finder import (
    CandidateContact,
    extract_emails,
    extract_people,
    guess_email_candidates,
)
from app.scraper.site_crawler import crawl_site

# A candidate is "good enough" to skip paid enrichment when it is a verified,
# non-guessed contact or any founder-role contact.
_GOOD_SOURCES = {"scraped", "api"}


@dataclass
class HuntResult:
    startup_id: int
    contacts_added: int
    enriched: bool


def _has_good_contact(candidates: list[CandidateContact]) -> bool:
    return any(c.found_via in _GOOD_SOURCES and c.email for c in candidates)


def hunt_startup(session: Session, startup: Startup, *, crawler=crawl_site,
                 founder_search=find_founder_names, enricher=None, resolver=None,
                 monthly_limit: int = 25) -> HuntResult:
    if not startup.domain:
        session.add(Event(startup_id=startup.id, kind="scrape_failed",
                          payload={"reason": "no_domain"}))
        session.commit()
        return HuntResult(startup_id=startup.id, contacts_added=0, enriched=False)

    candidates: list[CandidateContact] = []
    people = []

    # 1. Crawl + extract scraped emails and team people.
    for page in crawler(startup.domain):
        candidates.extend(extract_emails(page.html))
        people.extend(extract_people(page.html))

    # 2. Founder names: from team pages, else stored, else web gap-fill.
    founder_names = [p.name for p in people]
    if not founder_names:
        founder_names = list(startup.founder_names or [])
    if not founder_names:
        founder_names = founder_search(startup.name)

    person_roles = {p.name: p.role for p in people}

    # 3. Pattern-guess for each known person.
    for name in founder_names:
        candidates.extend(
            guess_email_candidates(name, startup.domain, role=person_roles.get(name, "Founder"))
        )

    # 4. Verify domains via MX.
    verify_candidates(candidates, resolver=resolver)

    # 5. Paid enrichment only if scraping + guessing produced nothing usable.
    if enricher is not None and not _has_good_contact(candidates):
        if usage.remaining(session, enricher.name, monthly_limit) > 0:
            usage.record_call(session, enricher.name)
            candidates.extend(enricher.domain_search(startup.domain))

    # 6. Rank and persist (dedupe against existing contacts for this startup).
    ranked = rank_contacts([c for c in candidates if c.email])
    existing = {
        c.email for c in session.scalars(
            select(Contact).where(Contact.startup_id == startup.id)
        )
    }
    added = 0
    for candidate in ranked:
        if candidate.email in existing:
            continue
        existing.add(candidate.email)
        session.add(Contact(
            startup_id=startup.id,
            name=candidate.name,
            role=candidate.role,
            email=candidate.email,
            found_via=candidate.found_via,
            confidence=candidate.confidence,
            verified=candidate.verified,
        ))
        added += 1

    if added > 0:
        startup.status = StartupStatus.ENRICHED
        session.add(Event(startup_id=startup.id, kind="enriched",
                          payload={"contacts_added": added}))
    else:
        session.add(Event(startup_id=startup.id, kind="scrape_failed",
                          payload={"reason": "no_contacts"}))
    session.commit()
    return HuntResult(startup_id=startup.id, contacts_added=added, enriched=added > 0)


def hunt_all(session: Session, *, limit: int = 50, crawler=crawl_site,
             founder_search=find_founder_names, enricher=None, resolver=None,
             monthly_limit: int = 25) -> list[HuntResult]:
    startups = session.scalars(
        select(Startup)
        .where(Startup.status == StartupStatus.DISCOVERED, Startup.domain.is_not(None))
        .limit(limit)
    ).all()
    results = []
    for startup in startups:
        results.append(hunt_startup(
            session, startup, crawler=crawler, founder_search=founder_search,
            enricher=enricher, resolver=resolver, monthly_limit=monthly_limit,
        ))
    return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_hunt.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add app/scraper/hunt.py tests/test_hunt.py
git commit -m "feat: hunt orchestration — crawl, guess, verify, enrich, rank, persist"
```

---

### Task 10: CLI `m2s hunt` + phase wrap-up

**Files:**
- Modify: `app/cli.py` (add `hunt` command, extend `stats` with contact counts)
- Modify: `README.md` (document phase 2)
- Test: `tests/test_cli_hunt.py`

**Interfaces:**
- Consumes: `hunt_all`, enrichment client construction from config, `app.config.get_settings`
- Produces:
  - `m2s hunt [--limit N] [--domain X] [--no-enrich]` — runs `hunt_all` over `discovered` startups (or a single one filtered by `--domain`); builds a `HunterClient` from `M2S_HUNTER_API_KEY` unless `--no-enrich` or the key is absent; prints per-startup results and a summary line `hunt: processed=N enriched=M contacts=K`.
  - `_build_enricher(settings) -> Enricher | None` helper in `app/cli.py`.

- [ ] **Step 1: Write the failing test**

`tests/test_cli_hunt.py`:

```python
from typer.testing import CliRunner

import app.cli as cli_mod
from app.cli import app
from app.scraper.hunt import HuntResult

runner = CliRunner()


def test_hunt_command_reports_summary(monkeypatch, tmp_path):
    monkeypatch.setenv("M2S_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("M2S_HUNTER_API_KEY", "")  # no enrichment
    runner.invoke(app, ["init-db"])

    captured = {}

    def fake_hunt_all(session, **kwargs):
        captured.update(kwargs)
        return [
            HuntResult(startup_id=1, contacts_added=2, enriched=True),
            HuntResult(startup_id=2, contacts_added=0, enriched=False),
        ]

    monkeypatch.setattr(cli_mod, "hunt_all", fake_hunt_all)
    result = runner.invoke(app, ["hunt", "--limit", "10", "--no-enrich"])
    assert result.exit_code == 0
    assert "processed=2" in result.output
    assert "enriched=1" in result.output
    assert "contacts=2" in result.output
    assert captured["enricher"] is None  # --no-enrich honored
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_cli_hunt.py -v`
Expected: FAIL with `AttributeError` / no `hunt` command (`result.exit_code != 0`)

- [ ] **Step 3: Write minimal implementation**

Add to `app/cli.py` (new imports at top, new command + helper; keep existing commands unchanged):

```python
from app.enrich.hunter import HunterClient
from app.scraper.hunt import hunt_all
from app.models import Contact
```

```python
def _build_enricher(settings):
    if settings.hunter_api_key:
        return HunterClient(api_key=settings.hunter_api_key)
    return None


@app.command()
def hunt(
    limit: int = typer.Option(50, help="Max discovered startups to process"),
    domain: str = typer.Option(None, help="Only hunt this single domain"),
    no_enrich: bool = typer.Option(False, help="Skip paid free-tier API enrichment"),
):
    """Find contact emails for discovered startups (crawl → guess → verify → enrich)."""
    settings = get_settings()
    enricher = None if no_enrich else _build_enricher(settings)
    with _session() as session:
        if domain:
            from app.models import Startup, StartupStatus
            startup = session.scalars(
                select(Startup).where(Startup.domain == domain)
            ).first()
            if startup is None:
                typer.echo(f"No startup with domain {domain}", err=True)
                raise typer.Exit(code=1)
            from app.scraper.hunt import hunt_startup
            results = [hunt_startup(session, startup, enricher=enricher,
                                    monthly_limit=settings.hunter_monthly_limit)]
        else:
            results = hunt_all(session, limit=limit, enricher=enricher,
                               monthly_limit=settings.hunter_monthly_limit)
    enriched = sum(1 for r in results if r.enriched)
    contacts = sum(r.contacts_added for r in results)
    for r in results:
        typer.echo(f"  startup {r.startup_id}: +{r.contacts_added} contacts "
                   f"({'enriched' if r.enriched else 'no contacts'})")
    typer.echo(f"hunt: processed={len(results)} enriched={enriched} contacts={contacts}")
```

Also extend `stats` to report contact counts by `found_via` (append before the function's end):

```python
    with _session() as session:
        by_found_via = session.execute(
            select(Contact.found_via, func.count()).group_by(Contact.found_via)).all()
    typer.echo("Contacts by source:")
    for found_via, count in by_found_via:
        typer.echo(f"  {found_via}: {count}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_cli_hunt.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python -m pytest -v`
Expected: ALL PASS (phase 1 ~29 + phase 2 ~28 = ~57 tests)

- [ ] **Step 6: Update README.md**

Under the Usage section, after the `m2s stats` line, add:

```markdown
## Email hunting (Phase 2)

```bash
m2s hunt --limit 50                 # crawl + guess + verify contacts for discovered startups
m2s hunt --domain acme.com          # hunt a single company
m2s hunt --limit 50 --no-enrich     # skip paid free-tier API fallback (Hunter)
```

Hunting is idempotent: each run processes only startups still in `discovered`
status and advances them to `enriched`. Paid enrichment (Hunter, ~25/mo free)
is consulted only when site crawling and pattern guessing find no usable
contact, and remaining monthly credits are tracked in the database.
```

Also change the status line near the top of the README from
`**Status: Phase 1 (foundation + startup discovery) complete.**`
to
`**Status: Phase 2 (email hunting & enrichment) complete.**`

- [ ] **Step 7: Commit**

```bash
git add app/cli.py README.md tests/test_cli_hunt.py
git commit -m "feat: m2s hunt CLI command and phase-2 docs"
```

---

### Task 11: Live smoke test (manual, online — not part of pytest)

**Files:** none (verification only)

**Interfaces:**
- Consumes: the full phase-2 build against real data already in `data/m2s.db` (55 startups from phase-1 smoke tests).

- [ ] **Step 1: Hunt a small batch against real sites**

Run:
```bash
.venv/Scripts/python -m app.cli hunt --limit 5 --no-enrich
.venv/Scripts/python -m app.cli stats
```
Expected: `hunt: processed=5 ...` with some `contacts>0`; `stats` shows startups moving from `discovered` to `enriched` and a "Contacts by source" breakdown. Real founder sites vary — a `contacts=0` on some is normal and correctly logged as `scrape_failed`.

- [ ] **Step 2: (Optional) Verify a single known domain end to end**

Run: `.venv/Scripts/python -m app.cli hunt --domain <one YC startup domain from the DB> --no-enrich`
Expected: prints `+N contacts`; inspect with a quick SQL/CLI check that at least one `Contact` row exists with a plausible `found_via`.

- [ ] **Step 3: Note results in the session handoff** (no commit — DB is gitignored). If any real site shape breaks extraction (e.g. emails only in rendered JS), record it for a possible Firecrawl-fallback follow-up; that fallback is intentionally deferred out of Phase 2 (see Self-review).

---

## Self-review notes

- **Spec coverage (Phase 2 "Email hunting & enrichment", items 1–6):**
  - (1) Site crawl `/ /about /team /contact /careers` → Task 5. (2) Extract mailto/plaintext/obfuscated emails + team names+roles → Tasks 1–2. (3) Founder-name gap-fill via exa, names only → Task 6. (4) Pattern guessing + MX verification with confidence → Tasks 3–4. (5) Free-tier enrichment (Hunter/Apollo) spent only after scraping+guessing fail, credits tracked in DB → Tasks 7 + 9. (6) Ranking scraped-founder > api > verified-guess > generic → Task 8. `m2s hunt` CLI → Task 10.
- **Deliberate scoping decisions (honest, not silent):**
  - **Firecrawl JS-fallback is deferred.** The spec lists it as a *fallback* for JS-heavy sites; the httpx crawler is the primary path and is fully tested. Wiring the Firecrawl CLI (subprocess + credit tracking) is a small, self-contained follow-up best done once we see which real sites actually need it (Task 11 Step 3 captures that signal). Flagged here rather than pretended-complete.
  - **Snov.io client omitted this phase.** Its OAuth token-exchange flow is materially more complex than Hunter's/Apollo's single-call APIs. The `Enricher` protocol (Task 7) is the drop-in seam; adding Snov later is one new file implementing `domain_search`. Hunter is wired end-to-end (client + credit tracking + CLI); Apollo ships as a second ready client.
  - **Per-address SMTP verification omitted.** It is unreliable and frequently blocked; MX-based domain deliverability (Task 4) is the free, deterministic signal, matching "MX lookup + free verifier APIs" without a flaky RCPT-TO probe.
- **Idempotency:** `hunt_all` processes only `discovered` startups and advances successes to `enriched`; contact writes dedupe by `(startup_id, email)`; enrichment calls are credit-gated and recorded before the call. Re-running never double-charges credits for already-enriched startups or duplicates contacts. Failures stay `discovered` (retryable) and log `scrape_failed`.
- **Type consistency:** one `CandidateContact` dataclass (defined Task 1) is produced by extraction, guessing, and both enrichment clients, consumed by `verify_candidates`, `rank_contacts`, and `hunt_startup`, and mapped to the ORM `Contact` at persist time. `found_via` uses exactly the Phase-1 vocabulary (`scraped|api|pattern_guess|generic`). `Enricher.domain_search(domain) -> list[CandidateContact]` is the single interface `HunterClient`, `ApolloClient`, and the `hunt` test fakes all satisfy.
- **Offline tests:** crawler (`crawler=`), founder search (`founder_search=`/`search_fn=`), DNS (`resolver=`), and enrichment (`enricher=`) are all injected; every HTTP client test uses `respx`. No test hits the network.
