# Mail2Startups Phase 1: Foundation & Startup Discovery — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the project foundation (config, SQLite schema, CLI) and the startup-discovery pipeline: five directory adapters that fill the `startups` table with deduplicated companies from YC, Product Hunt, Startup India, listicle articles, and CSV files.

**Architecture:** Single Python package `app/` with SQLAlchemy models over SQLite, a pluggable `Source` adapter interface under `app/scraper/sources/`, an idempotent ingest function that dedupes by normalized domain, and a Typer CLI (`m2s`). Phases 2–4 (email hunting, AI drafting, sending/dashboard) get their own plans later and build on the schema defined here.

**Tech Stack:** Python 3.12+, SQLAlchemy 2.x, pydantic-settings, httpx, BeautifulSoup4, Typer, anthropic SDK (structured outputs), pytest + respx.

## Global Constraints

- Python 3.12+; the dev machine is Windows 11 with Git Bash — use `python -m pytest`, forward slashes, no OS-specific paths in code (use `pathlib`).
- Tests are fully offline: HTTP is mocked with `respx`; the Anthropic client is injected and faked in tests. No live network calls in the test suite.
- Startup status values exactly as specced: `discovered, enriched, drafted, in_review, queued, sent, replied, bounced, no_response, dead`.
- All settings come from `app.config.Settings` (env prefix `M2S_`, loaded from `.env`); the Anthropic SDK reads `ANTHROPIC_API_KEY` from the environment itself — never hardcode keys.
- Claude model default: `claude-opus-5` (override with `M2S_ANTHROPIC_MODEL`). Use `client.messages.parse()` with a Pydantic `output_format` — never parse freeform JSON from text.
- External endpoint constants live at the top of each adapter module so they can be updated in one place if an API changes.
- Commit after every task; conventional-commit style messages; end each commit message with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Project scaffold and config

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `app/__init__.py`
- Create: `app/config.py`
- Create: `tests/__init__.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing (first task)
- Produces: `app.config.Settings` (fields: `db_path: Path`, `anthropic_model: str`, `product_hunt_token: str`) and `app.config.get_settings() -> Settings` (re-reads env each call — no caching, so tests can override).

- [ ] **Step 1: Create pyproject.toml, .gitignore, .env.example**

`pyproject.toml`:

```toml
[project]
name = "mail2startups"
version = "0.1.0"
description = "Automated internship outreach: startup discovery, email hunting, AI drafting, drip sending"
requires-python = ">=3.12"
dependencies = [
    "sqlalchemy>=2.0",
    "pydantic-settings>=2.0",
    "httpx>=0.27",
    "beautifulsoup4>=4.12",
    "typer>=0.12",
    "anthropic>=0.60",
    "python-dotenv>=1.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "respx>=0.21"]

[project.scripts]
m2s = "app.cli:app"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools]
packages = { find = { include = ["app*"] } }

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`.gitignore`:

```gitignore
.venv/
__pycache__/
*.pyc
.env
data/m2s.db
out/
.pytest_cache/
*.egg-info/
.firecrawl/
```

`.env.example`:

```bash
# Anthropic SDK reads this directly
ANTHROPIC_API_KEY=sk-ant-...

# App settings (all optional, defaults shown)
M2S_DB_PATH=data/m2s.db
M2S_ANTHROPIC_MODEL=claude-opus-5
M2S_PRODUCT_HUNT_TOKEN=
```

Create empty `app/__init__.py` and `tests/__init__.py`.

- [ ] **Step 2: Write the failing test**

`tests/test_config.py`:

```python
from pathlib import Path

from app.config import get_settings


def test_defaults():
    s = get_settings()
    assert s.db_path == Path("data/m2s.db")
    assert s.anthropic_model == "claude-opus-5"
    assert s.product_hunt_token == ""


def test_env_override(monkeypatch):
    monkeypatch.setenv("M2S_DB_PATH", "elsewhere/test.db")
    monkeypatch.setenv("M2S_ANTHROPIC_MODEL", "claude-sonnet-5")
    s = get_settings()
    assert s.db_path == Path("elsewhere/test.db")
    assert s.anthropic_model == "claude-sonnet-5"
```

- [ ] **Step 3: Set up venv, install, run test to verify it fails**

Run:
```bash
python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pytest tests/test_config.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'app.config'`

- [ ] **Step 4: Write minimal implementation**

`app/config.py`:

```python
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()  # makes ANTHROPIC_API_KEY from .env visible to the anthropic SDK


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="M2S_", env_file=".env", extra="ignore")

    db_path: Path = Path("data/m2s.db")
    anthropic_model: str = "claude-opus-5"
    product_hunt_token: str = ""


def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_config.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .gitignore .env.example app/ tests/
git commit -m "feat: project scaffold with config module"
```

---

### Task 2: Database models and init

**Files:**
- Create: `app/models.py`
- Create: `app/db.py`
- Create: `tests/conftest.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: `app.config.get_settings`
- Produces:
  - `app.models.StartupStatus` (str enum), `DraftStatus`, `DraftMode`, `MessageStatus`, `MessageType` (str enums)
  - ORM classes `Startup`, `Contact`, `Draft`, `Message`, `Event` with fields listed below
  - `app.db.get_engine(db_path: Path | str) -> Engine`, `app.db.init_db(engine) -> None`, `app.db.make_session(engine) -> Session`
- Later phases rely on: `Startup.status` transitions, `Contact.found_via` values (`scraped|api|pattern_guess|generic`), `Draft.mode` (`formal|casual`), `Message.smtp_message_id`.

- [ ] **Step 1: Write the failing test**

`tests/conftest.py`:

```python
import pytest

from app.db import get_engine, init_db, make_session


@pytest.fixture()
def session():
    engine = get_engine(":memory:")
    init_db(engine)
    s = make_session(engine)
    yield s
    s.close()
```

`tests/test_models.py`:

```python
import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Contact, Draft, Event, Message, Startup, StartupStatus


def test_startup_defaults(session):
    s = Startup(name="Acme", domain="acme.com", source="csv")
    session.add(s)
    session.commit()
    assert s.id is not None
    assert s.status == StartupStatus.DISCOVERED
    assert s.created_at is not None


def test_domain_unique(session):
    session.add(Startup(name="A", domain="acme.com", source="csv"))
    session.commit()
    session.add(Startup(name="B", domain="acme.com", source="yc"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_contact_links_startup(session):
    s = Startup(name="Acme", domain="acme.com", source="csv")
    session.add(s)
    session.flush()
    c = Contact(startup_id=s.id, name="Jane Roe", role="founder",
                email="jane@acme.com", found_via="scraped", confidence=0.9)
    session.add(c)
    session.commit()
    assert s.contacts[0].email == "jane@acme.com"


def test_all_tables_exist(session):
    # Draft, Message, Event are created now as the schema contract for later phases
    session.add(Startup(name="X", domain="x.io", source="csv"))
    session.flush()
    for model in (Draft, Message, Event):
        assert model.__tablename__
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.db'`

- [ ] **Step 3: Write minimal implementation**

`app/models.py`:

```python
import enum
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class StartupStatus(str, enum.Enum):
    DISCOVERED = "discovered"
    ENRICHED = "enriched"
    DRAFTED = "drafted"
    IN_REVIEW = "in_review"
    QUEUED = "queued"
    SENT = "sent"
    REPLIED = "replied"
    BOUNCED = "bounced"
    NO_RESPONSE = "no_response"
    DEAD = "dead"


class DraftMode(str, enum.Enum):
    FORMAL = "formal"
    CASUAL = "casual"


class DraftStatus(str, enum.Enum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class MessageType(str, enum.Enum):
    INITIAL = "initial"
    FOLLOWUP = "followup"


class MessageStatus(str, enum.Enum):
    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"
    BOUNCED = "bounced"
    REPLIED = "replied"


class Startup(Base):
    __tablename__ = "startups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    domain: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source: Mapped[str] = mapped_column(String(50))
    location: Mapped[str] = mapped_column(String(200), default="")
    industry: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    team_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    founder_names: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[StartupStatus] = mapped_column(
        Enum(StartupStatus, values_callable=lambda e: [m.value for m in e]),
        default=StartupStatus.DISCOVERED,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    contacts: Mapped[list["Contact"]] = relationship(back_populates="startup")


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    startup_id: Mapped[int] = mapped_column(ForeignKey("startups.id"))
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    role: Mapped[str] = mapped_column(String(100), default="")
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    found_via: Mapped[str] = mapped_column(String(30), default="scraped")  # scraped|api|pattern_guess|generic
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    verified: Mapped[bool] = mapped_column(default=False)

    startup: Mapped["Startup"] = relationship(back_populates="contacts")


class Draft(Base):
    __tablename__ = "drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    startup_id: Mapped[int] = mapped_column(ForeignKey("startups.id"))
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"), nullable=True)
    mode: Mapped[DraftMode] = mapped_column(
        Enum(DraftMode, values_callable=lambda e: [m.value for m in e]),
        default=DraftMode.FORMAL,
    )
    subject: Mapped[str] = mapped_column(String(300), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    resume_pdf_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[DraftStatus] = mapped_column(
        Enum(DraftStatus, values_callable=lambda e: [m.value for m in e]),
        default=DraftStatus.PENDING_REVIEW,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    draft_id: Mapped[int] = mapped_column(ForeignKey("drafts.id"))
    type: Mapped[MessageType] = mapped_column(
        Enum(MessageType, values_callable=lambda e: [m.value for m in e]),
        default=MessageType.INITIAL,
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    smtp_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[MessageStatus] = mapped_column(
        Enum(MessageStatus, values_callable=lambda e: [m.value for m in e]),
        default=MessageStatus.QUEUED,
    )


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    startup_id: Mapped[int | None] = mapped_column(ForeignKey("startups.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(50))  # e.g. discovered, bounce, reply, error, retry, pause
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
```

`app/db.py`:

```python
from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from app.models import Base


def get_engine(db_path: Path | str) -> Engine:
    if str(db_path) == ":memory:":
        return create_engine("sqlite+pysqlite:///:memory:")
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite+pysqlite:///{p}")


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)


def make_session(engine: Engine) -> Session:
    return Session(engine)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_models.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/models.py app/db.py tests/conftest.py tests/test_models.py
git commit -m "feat: SQLite schema for startups, contacts, drafts, messages, events"
```

---

### Task 3: StartupRecord, Source protocol, domain normalization

**Files:**
- Create: `app/scraper/__init__.py` (empty)
- Create: `app/scraper/sources/base.py`
- Test: `tests/test_normalize.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `StartupRecord` dataclass: `name: str`, `website: str | None = None`, `domain: str | None = None` (auto-derived from website in `__post_init__` when not given), `description: str = ""`, `location: str = ""`, `industry: str = ""`, `team_size: int | None = None`, `founder_names: list[str]` (default empty), `contact_emails: list[str]` (default empty), `source: str = ""`
  - `normalize_domain(value: str | None) -> str | None`
  - `Source` Protocol: attribute `name: str`; method `fetch(self, limit: int = 100, **filters) -> list[StartupRecord]`

- [ ] **Step 1: Write the failing test**

`tests/test_normalize.py`:

```python
from app.scraper.sources.base import StartupRecord, normalize_domain


def test_normalize_domain_variants():
    assert normalize_domain("https://www.acme.com/about") == "acme.com"
    assert normalize_domain("http://acme.com") == "acme.com"
    assert normalize_domain("ACME.COM") == "acme.com"
    assert normalize_domain("www.acme.co.in/") == "acme.co.in"
    assert normalize_domain("acme.com") == "acme.com"
    assert normalize_domain("") is None
    assert normalize_domain(None) is None
    assert normalize_domain("not a domain") is None


def test_record_derives_domain():
    r = StartupRecord(name="Acme", website="https://www.acme.com/x")
    assert r.domain == "acme.com"


def test_record_explicit_domain_wins():
    r = StartupRecord(name="Acme", website="https://redirect.example.com", domain="acme.com")
    assert r.domain == "acme.com"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_normalize.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

`app/scraper/sources/base.py` (also create empty `app/scraper/__init__.py`; `app/scraper/sources/__init__.py` is created in Task 5 — for now create it empty):

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_normalize.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/scraper/ tests/test_normalize.py
git commit -m "feat: StartupRecord, Source protocol, domain normalization"
```

---

### Task 4: Idempotent ingest with domain dedup

**Files:**
- Create: `app/scraper/ingest.py`
- Test: `tests/test_ingest.py`

**Interfaces:**
- Consumes: `StartupRecord`, ORM models, a SQLAlchemy `Session`
- Produces: `IngestResult` dataclass (`added: int`, `skipped: int`) and `ingest_records(session, records: list[StartupRecord]) -> IngestResult`. Dedup key: normalized `domain`; records with no domain dedupe on lowercased `name`. Each inserted startup gets contacts from `contact_emails` (found_via `scraped`, confidence 0.5) and an `Event(kind="discovered")`.

- [ ] **Step 1: Write the failing test**

`tests/test_ingest.py`:

```python
from sqlalchemy import select

from app.models import Contact, Event, Startup
from app.scraper.ingest import ingest_records
from app.scraper.sources.base import StartupRecord


def _records():
    return [
        StartupRecord(name="Acme", website="https://www.acme.com", source="yc",
                      contact_emails=["hello@acme.com"], founder_names=["Jane Roe"]),
        StartupRecord(name="Beta Labs", website="https://beta.io", source="yc"),
    ]


def test_ingest_inserts(session):
    result = ingest_records(session, _records())
    assert result.added == 2 and result.skipped == 0
    acme = session.scalars(select(Startup).where(Startup.domain == "acme.com")).one()
    assert acme.founder_names == ["Jane Roe"]
    contact = session.scalars(select(Contact).where(Contact.startup_id == acme.id)).one()
    assert contact.email == "hello@acme.com" and contact.found_via == "scraped"
    assert session.scalars(select(Event).where(Event.kind == "discovered")).all()


def test_ingest_is_idempotent(session):
    ingest_records(session, _records())
    result = ingest_records(session, _records())
    assert result.added == 0 and result.skipped == 2
    assert len(session.scalars(select(Startup)).all()) == 2


def test_dedup_across_sources_by_domain(session):
    ingest_records(session, [StartupRecord(name="Acme", website="https://acme.com", source="yc")])
    result = ingest_records(session, [StartupRecord(name="ACME Inc", website="http://www.acme.com/", source="csv")])
    assert result.added == 0 and result.skipped == 1


def test_no_domain_dedupes_by_name(session):
    ingest_records(session, [StartupRecord(name="Stealth Co", source="listicle")])
    result = ingest_records(session, [StartupRecord(name="stealth co", source="listicle")])
    assert result.added == 0 and result.skipped == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_ingest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.scraper.ingest'`

- [ ] **Step 3: Write minimal implementation**

`app/scraper/ingest.py`:

```python
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Contact, Event, Startup
from app.scraper.sources.base import StartupRecord


@dataclass
class IngestResult:
    added: int = 0
    skipped: int = 0


def _exists(session: Session, record: StartupRecord) -> bool:
    if record.domain:
        q = select(Startup.id).where(Startup.domain == record.domain)
    else:
        q = select(Startup.id).where(
            Startup.domain.is_(None), func.lower(Startup.name) == record.name.lower()
        )
    return session.scalars(q).first() is not None


def ingest_records(session: Session, records: list[StartupRecord]) -> IngestResult:
    result = IngestResult()
    for record in records:
        if _exists(session, record):
            result.skipped += 1
            continue
        startup = Startup(
            name=record.name,
            domain=record.domain,
            website=record.website,
            source=record.source,
            location=record.location,
            industry=record.industry,
            description=record.description,
            team_size=record.team_size,
            founder_names=list(record.founder_names),
        )
        session.add(startup)
        session.flush()
        for email in record.contact_emails:
            session.add(Contact(startup_id=startup.id, email=email,
                                found_via="scraped", confidence=0.5))
        session.add(Event(startup_id=startup.id, kind="discovered",
                          payload={"source": record.source}))
        result.added += 1
    session.commit()
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_ingest.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/scraper/ingest.py tests/test_ingest.py
git commit -m "feat: idempotent ingest with domain-based dedup"
```

---

### Task 5: CSV adapter and source registry

**Files:**
- Create: `app/scraper/sources/csv_file.py`
- Modify: `app/scraper/sources/__init__.py`
- Test: `tests/test_sources_csv.py`

**Interfaces:**
- Consumes: `StartupRecord`
- Produces:
  - `CsvSource` with `name = "csv"`, `fetch(limit=100, path=None) -> list[StartupRecord]`. CSV columns: `name` (required), `website`, `description`, `location`, `industry`, `founder_names` (semicolon-separated), `emails` (semicolon-separated). Unknown columns ignored; rows without a name skipped.
  - Registry in `app/scraper/sources/__init__.py`: `SOURCES: dict[str, type]` and `get_source(name: str) -> Source` (instantiates; raises `ValueError` listing valid names for unknown ones). Tasks 6–9 each append their adapter class to `SOURCES`.

- [ ] **Step 1: Write the failing test**

`tests/test_sources_csv.py`:

```python
import pytest

from app.scraper.sources import get_source


CSV_TEXT = """name,website,description,location,industry,founder_names,emails
Acme,https://www.acme.com,Widgets,Bangalore,SaaS,Jane Roe;Raj Kumar,hello@acme.com;jane@acme.com
Beta Labs,beta.io,,,,,
,missing-name.com,,,,,
"""


def test_csv_fetch(tmp_path):
    p = tmp_path / "list.csv"
    p.write_text(CSV_TEXT, encoding="utf-8")
    records = get_source("csv").fetch(path=str(p))
    assert len(records) == 2  # nameless row skipped
    acme = records[0]
    assert acme.domain == "acme.com"
    assert acme.founder_names == ["Jane Roe", "Raj Kumar"]
    assert acme.contact_emails == ["hello@acme.com", "jane@acme.com"]
    assert acme.source == "csv"


def test_csv_requires_path():
    with pytest.raises(ValueError):
        get_source("csv").fetch()


def test_unknown_source():
    with pytest.raises(ValueError, match="csv"):
        get_source("nope")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_sources_csv.py -v`
Expected: FAIL with `ImportError: cannot import name 'get_source'`

- [ ] **Step 3: Write minimal implementation**

`app/scraper/sources/csv_file.py`:

```python
import csv
from pathlib import Path

from app.scraper.sources.base import StartupRecord


def _split(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split(";") if part.strip()]


class CsvSource:
    name = "csv"

    def fetch(self, limit: int = 100_000, path: str | None = None, **filters) -> list[StartupRecord]:
        if not path:
            raise ValueError("csv source requires path=<file.csv>")
        records: list[StartupRecord] = []
        with Path(path).open(newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                name = (row.get("name") or "").strip()
                if not name:
                    continue
                records.append(StartupRecord(
                    name=name,
                    website=(row.get("website") or "").strip() or None,
                    description=(row.get("description") or "").strip(),
                    location=(row.get("location") or "").strip(),
                    industry=(row.get("industry") or "").strip(),
                    founder_names=_split(row.get("founder_names")),
                    contact_emails=_split(row.get("emails")),
                    source=self.name,
                ))
                if len(records) >= limit:
                    break
        return records
```

`app/scraper/sources/__init__.py`:

```python
from app.scraper.sources.base import Source
from app.scraper.sources.csv_file import CsvSource

SOURCES: dict[str, type] = {
    CsvSource.name: CsvSource,
}


def get_source(name: str) -> Source:
    try:
        return SOURCES[name]()
    except KeyError:
        valid = ", ".join(sorted(SOURCES))
        raise ValueError(f"Unknown source '{name}'. Valid sources: {valid}") from None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_sources_csv.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/scraper/sources/ tests/test_sources_csv.py
git commit -m "feat: CSV import adapter and source registry"
```

---

### Task 6: Y Combinator adapter (yc-oss static JSON)

**Files:**
- Create: `app/scraper/sources/yc.py`
- Modify: `app/scraper/sources/__init__.py` (register `YcSource`)
- Test: `tests/test_sources_yc.py`

**Interfaces:**
- Consumes: `StartupRecord`, httpx
- Produces: `YcSource` with `name = "yc"`, `fetch(limit=100, list_name="hiring", region=None) -> list[StartupRecord]`. `list_name` ∈ `all|top|hiring` fetches `https://yc-oss.github.io/api/companies/{list_name}.json` (community mirror of YC's directory, refreshed daily). Filters: only `status == "Active"`; `region` substring-matches against `all_locations` + `regions` (case-insensitive) — e.g. `region="india"` or `region="remote"`.

- [ ] **Step 1: Write the failing test**

`tests/test_sources_yc.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_sources_yc.py -v`
Expected: FAIL with `ValueError: Unknown source 'yc'`

- [ ] **Step 3: Write minimal implementation**

`app/scraper/sources/yc.py`:

```python
import httpx

from app.scraper.sources.base import StartupRecord

BASE_URL = "https://yc-oss.github.io/api/companies/{list_name}.json"
VALID_LISTS = {"all", "top", "hiring"}


class YcSource:
    name = "yc"

    def fetch(self, limit: int = 100, list_name: str = "hiring",
              region: str | None = None, **filters) -> list[StartupRecord]:
        if list_name not in VALID_LISTS:
            raise ValueError(f"list_name must be one of {sorted(VALID_LISTS)}")
        resp = httpx.get(BASE_URL.format(list_name=list_name), timeout=30)
        resp.raise_for_status()
        records: list[StartupRecord] = []
        for company in resp.json():
            if company.get("status") != "Active":
                continue
            if region:
                haystack = " ".join(
                    [company.get("all_locations") or ""] + (company.get("regions") or [])
                ).lower()
                if region.lower() not in haystack:
                    continue
            description = ". ".join(
                part for part in
                [company.get("one_liner") or "", company.get("long_description") or ""]
                if part
            )
            records.append(StartupRecord(
                name=company.get("name") or "",
                website=company.get("website") or None,
                description=description,
                location=company.get("all_locations") or "",
                industry=", ".join(company.get("industries") or []) or (company.get("industry") or ""),
                team_size=company.get("team_size") or None,
                source=self.name,
            ))
            if len(records) >= limit:
                break
        return records
```

Register in `app/scraper/sources/__init__.py`:

```python
from app.scraper.sources.base import Source
from app.scraper.sources.csv_file import CsvSource
from app.scraper.sources.yc import YcSource

SOURCES: dict[str, type] = {
    CsvSource.name: CsvSource,
    YcSource.name: YcSource,
}
```

(keep the existing `get_source` function unchanged)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_sources_yc.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/scraper/sources/yc.py app/scraper/sources/__init__.py tests/test_sources_yc.py
git commit -m "feat: YC directory adapter via yc-oss static JSON"
```

---

### Task 7: Startup India adapter

**Files:**
- Create: `app/scraper/sources/startup_india.py`
- Modify: `app/scraper/sources/__init__.py` (register `StartupIndiaSource`)
- Test: `tests/test_sources_startup_india.py`

**Interfaces:**
- Consumes: `StartupRecord`, httpx
- Produces: `StartupIndiaSource` with `name = "startup_india"`, `fetch(limit=50, query="", fetch_profiles=True) -> list[StartupRecord]`. POSTs to the public search API with browser-mimicking headers (required — the API ignores non-browser requests), paginates through `content[]`, and (when `fetch_profiles`) GETs each profile for website/email. Profile JSON shape varies, so extraction uses a recursive key search helper. A `time.sleep(0.4)` between profile fetches keeps it polite (module-level `PROFILE_DELAY_SECONDS` constant; tests set it to 0).

- [ ] **Step 1: Write the failing test**

`tests/test_sources_startup_india.py`:

```python
import respx
from httpx import Response

from app.scraper.sources import get_source
from app.scraper.sources import startup_india

SEARCH_FIXTURE = {
    "content": [
        {"id": "p1", "name": "DesiTech", "country": "India", "state": "Karnataka", "city": "Bengaluru"},
        {"id": "p2", "name": "AgriNext", "country": "India", "state": "Maharashtra", "city": "Pune"},
    ],
    "totalPages": 1, "totalElements": 2, "number": 0,
}

PROFILE_P1 = {"user": {"startup": {"website": "https://desitech.in",
                                   "email": "founder@desitech.in",
                                   "sector": "FinTech"}}}
PROFILE_P2 = {"user": {"startup": {}}}


@respx.mock
def test_startup_india_fetch(monkeypatch):
    monkeypatch.setattr(startup_india, "PROFILE_DELAY_SECONDS", 0)
    respx.post(startup_india.SEARCH_URL).mock(return_value=Response(200, json=SEARCH_FIXTURE))
    respx.get(startup_india.PROFILE_URL.format(profile_id="p1")).mock(
        return_value=Response(200, json=PROFILE_P1))
    respx.get(startup_india.PROFILE_URL.format(profile_id="p2")).mock(
        return_value=Response(200, json=PROFILE_P2))

    records = get_source("startup_india").fetch(limit=10)
    assert len(records) == 2
    desi = records[0]
    assert desi.name == "DesiTech"
    assert desi.location == "Bengaluru, Karnataka"
    assert desi.domain == "desitech.in"
    assert desi.contact_emails == ["founder@desitech.in"]
    assert desi.industry == "FinTech"
    # profile without website/email still yields a record
    assert records[1].domain is None


def test_find_first_key():
    data = {"a": {"b": [{"website": "https://x.io"}]}}
    assert startup_india._find_first(data, ("website",)) == "https://x.io"
    assert startup_india._find_first(data, ("missing",)) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_sources_startup_india.py -v`
Expected: FAIL with `ImportError` / `ValueError: Unknown source 'startup_india'`

- [ ] **Step 3: Write minimal implementation**

`app/scraper/sources/startup_india.py`:

```python
import time

import httpx

from app.scraper.sources.base import StartupRecord

SEARCH_URL = "https://api.startupindia.gov.in/sih/api/noauth/search/profiles"
PROFILE_URL = "https://api.startupindia.gov.in/sih/api/common/replica/user/profile/{profile_id}"
PROFILE_DELAY_SECONDS = 0.4

# The API only responds to browser-like requests.
BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"),
    "Origin": "https://www.startupindia.gov.in",
    "Referer": "https://www.startupindia.gov.in/",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/json",
}


def _find_first(data, keys: tuple[str, ...]):
    """Depth-first search for the first non-empty value under any of `keys`."""
    if isinstance(data, dict):
        for key, value in data.items():
            if key in keys and value:
                return value
            found = _find_first(value, keys)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _find_first(item, keys)
            if found:
                return found
    return None


class StartupIndiaSource:
    name = "startup_india"

    def fetch(self, limit: int = 50, query: str = "",
              fetch_profiles: bool = True, **filters) -> list[StartupRecord]:
        records: list[StartupRecord] = []
        page = 0
        with httpx.Client(headers=BROWSER_HEADERS, timeout=30) as client:
            while len(records) < limit:
                resp = client.post(SEARCH_URL, json={
                    "query": query, "roles": ["Startup"], "page": page,
                })
                resp.raise_for_status()
                data = resp.json()
                for entry in data.get("content", []):
                    website = email = industry = None
                    if fetch_profiles and entry.get("id"):
                        try:
                            profile = client.get(
                                PROFILE_URL.format(profile_id=entry["id"])
                            ).json()
                            website = _find_first(profile, ("website", "websiteUrl"))
                            email = _find_first(profile, ("email", "emailId"))
                            industry = _find_first(profile, ("sector", "industry"))
                        except (httpx.HTTPError, ValueError):
                            pass
                        time.sleep(PROFILE_DELAY_SECONDS)
                    location = ", ".join(
                        part for part in [entry.get("city"), entry.get("state")] if part
                    )
                    records.append(StartupRecord(
                        name=entry.get("name") or "",
                        website=website,
                        location=location,
                        industry=industry or "",
                        contact_emails=[email] if email else [],
                        source=self.name,
                    ))
                    if len(records) >= limit:
                        break
                page += 1
                if page >= int(data.get("totalPages") or 1):
                    break
        return records
```

Register `StartupIndiaSource` in `app/scraper/sources/__init__.py` (add the import and the `SOURCES` entry, same pattern as Task 6).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_sources_startup_india.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/scraper/sources/startup_india.py app/scraper/sources/__init__.py tests/test_sources_startup_india.py
git commit -m "feat: Startup India directory adapter"
```

---

### Task 8: Product Hunt adapter

**Files:**
- Create: `app/scraper/sources/product_hunt.py`
- Modify: `app/scraper/sources/__init__.py` (register `ProductHuntSource`)
- Test: `tests/test_sources_product_hunt.py`

**Interfaces:**
- Consumes: `StartupRecord`, httpx, `app.config.get_settings` (for `product_hunt_token`)
- Produces: `ProductHuntSource` with `name = "product_hunt"`, `fetch(limit=50, topic=None) -> list[StartupRecord]`. POSTs a GraphQL query to `https://api.producthunt.com/v2/api/graphql` with `Authorization: Bearer <token>`. Raises `ValueError` with setup instructions when the token is missing.

- [ ] **Step 1: Write the failing test**

`tests/test_sources_product_hunt.py`:

```python
import pytest
import respx
from httpx import Response

from app.scraper.sources import get_source
from app.scraper.sources import product_hunt

PH_FIXTURE = {
    "data": {"posts": {"edges": [
        {"node": {"name": "LaunchPad", "tagline": "Ship faster",
                  "description": "A tool to ship faster.",
                  "website": "https://launchpad.dev",
                  "topics": {"edges": [{"node": {"name": "Developer Tools"}}]}}},
    ]}}
}


@respx.mock
def test_product_hunt_fetch(monkeypatch):
    monkeypatch.setenv("M2S_PRODUCT_HUNT_TOKEN", "test-token")
    route = respx.post(product_hunt.GRAPHQL_URL).mock(
        return_value=Response(200, json=PH_FIXTURE))
    records = get_source("product_hunt").fetch(limit=5, topic="developer-tools")
    assert route.called
    assert route.calls[0].request.headers["authorization"] == "Bearer test-token"
    assert len(records) == 1
    r = records[0]
    assert r.name == "LaunchPad"
    assert r.domain == "launchpad.dev"
    assert r.industry == "Developer Tools"
    assert r.source == "product_hunt"


def test_missing_token_raises(monkeypatch):
    monkeypatch.delenv("M2S_PRODUCT_HUNT_TOKEN", raising=False)
    with pytest.raises(ValueError, match="M2S_PRODUCT_HUNT_TOKEN"):
        get_source("product_hunt").fetch()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_sources_product_hunt.py -v`
Expected: FAIL with `ValueError: Unknown source 'product_hunt'`

- [ ] **Step 3: Write minimal implementation**

`app/scraper/sources/product_hunt.py`:

```python
import httpx

from app.config import get_settings
from app.scraper.sources.base import StartupRecord

GRAPHQL_URL = "https://api.producthunt.com/v2/api/graphql"

_QUERY = """
query Posts($first: Int!, $topic: String) {
  posts(first: $first, topic: $topic, order: VOTES) {
    edges { node {
      name tagline description website
      topics(first: 3) { edges { node { name } } }
    } }
  }
}
"""


class ProductHuntSource:
    name = "product_hunt"

    def fetch(self, limit: int = 50, topic: str | None = None, **filters) -> list[StartupRecord]:
        token = get_settings().product_hunt_token
        if not token:
            raise ValueError(
                "Product Hunt requires an API token. Create one at "
                "https://www.producthunt.com/v2/oauth/applications and set "
                "M2S_PRODUCT_HUNT_TOKEN in .env"
            )
        resp = httpx.post(
            GRAPHQL_URL,
            json={"query": _QUERY, "variables": {"first": min(limit, 50), "topic": topic}},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        resp.raise_for_status()
        edges = resp.json().get("data", {}).get("posts", {}).get("edges", [])
        records: list[StartupRecord] = []
        for edge in edges[:limit]:
            node = edge.get("node", {})
            topics = [t["node"]["name"] for t in node.get("topics", {}).get("edges", [])]
            records.append(StartupRecord(
                name=node.get("name") or "",
                website=node.get("website") or None,
                description=". ".join(p for p in [node.get("tagline"), node.get("description")] if p),
                industry=", ".join(topics),
                source=self.name,
            ))
        return records
```

Register `ProductHuntSource` in `app/scraper/sources/__init__.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_sources_product_hunt.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/scraper/sources/product_hunt.py app/scraper/sources/__init__.py tests/test_sources_product_hunt.py
git commit -m "feat: Product Hunt GraphQL adapter"
```

---

### Task 9: Claude extraction client and listicle adapter

**Files:**
- Create: `app/ai.py`
- Create: `app/scraper/sources/listicle.py`
- Modify: `app/scraper/sources/__init__.py` (register `ListicleSource`)
- Test: `tests/test_ai_extract.py`
- Test: `tests/test_sources_listicle.py`

**Interfaces:**
- Consumes: anthropic SDK, `app.config.get_settings`, httpx + BeautifulSoup
- Produces:
  - `app.ai.ExtractedStartup` (Pydantic: `name: str`, `website: str | None`, `description: str | None`) and `app.ai.StartupList` (`startups: list[ExtractedStartup]`)
  - `app.ai.extract_startups_from_text(text: str, client=None) -> list[ExtractedStartup]` — `client` injectable for tests; defaults to `anthropic.Anthropic()`. Uses `client.messages.parse(..., output_format=StartupList)` and returns `response.parsed_output.startups`.
  - `ListicleSource` with `name = "listicle"`, `fetch(limit=100, url=None) -> list[StartupRecord]` — fetches the article HTML, strips it to text with BeautifulSoup, calls `extract_startups_from_text`.
- Phase 3 reuses `app/ai.py` as the home for all Claude calls.

- [ ] **Step 1: Write the failing tests**

`tests/test_ai_extract.py`:

```python
from types import SimpleNamespace

from app.ai import ExtractedStartup, StartupList, extract_startups_from_text


class FakeMessages:
    def __init__(self, result):
        self._result = result
        self.last_kwargs = None

    def parse(self, **kwargs):
        self.last_kwargs = kwargs
        return SimpleNamespace(parsed_output=self._result, stop_reason="end_turn")


def test_extract_returns_startups():
    expected = StartupList(startups=[
        ExtractedStartup(name="Acme", website="https://acme.com", description="widgets"),
    ])
    fake_client = SimpleNamespace(messages=FakeMessages(expected))
    result = extract_startups_from_text("Top startups: 1. Acme (acme.com) ...",
                                        client=fake_client)
    assert result == expected.startups
    kwargs = fake_client.messages.last_kwargs
    assert kwargs["output_format"] is StartupList
    assert "Top startups" in kwargs["messages"][0]["content"]


def test_extract_truncates_huge_input():
    expected = StartupList(startups=[])
    fake_client = SimpleNamespace(messages=FakeMessages(expected))
    extract_startups_from_text("x" * 500_000, client=fake_client)
    sent = fake_client.messages.last_kwargs["messages"][0]["content"]
    assert len(sent) < 250_000
```

`tests/test_sources_listicle.py`:

```python
import respx
from httpx import Response

from app.ai import ExtractedStartup
from app.scraper.sources import get_source
from app.scraper.sources import listicle


HTML = "<html><body><h1>Top 2 startups</h1><p>1. Acme - acme.com</p><script>junk()</script></body></html>"


@respx.mock
def test_listicle_fetch(monkeypatch):
    respx.get("https://example.com/top-startups").mock(
        return_value=Response(200, text=HTML))

    def fake_extract(text, client=None):
        assert "Acme" in text
        assert "junk()" not in text  # scripts stripped
        return [ExtractedStartup(name="Acme", website="https://acme.com",
                                 description="widgets")]

    monkeypatch.setattr(listicle, "extract_startups_from_text", fake_extract)
    records = get_source("listicle").fetch(url="https://example.com/top-startups")
    assert len(records) == 1
    assert records[0].domain == "acme.com"
    assert records[0].source == "listicle"


def test_listicle_requires_url():
    import pytest
    with pytest.raises(ValueError):
        get_source("listicle").fetch()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_ai_extract.py tests/test_sources_listicle.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ai'`

- [ ] **Step 3: Write minimal implementation**

`app/ai.py`:

```python
import anthropic
from pydantic import BaseModel

from app.config import get_settings

MAX_INPUT_CHARS = 200_000


class ExtractedStartup(BaseModel):
    name: str
    website: str | None = None
    description: str | None = None


class StartupList(BaseModel):
    startups: list[ExtractedStartup]


_EXTRACT_PROMPT = (
    "The following is the text of a web article listing startups. Extract every "
    "startup company mentioned. For each, give its name, its website URL if the "
    "article states or clearly implies one (otherwise null), and a one-sentence "
    "description from the article. Do not invent companies or URLs.\n\n"
    "ARTICLE TEXT:\n{text}"
)


def extract_startups_from_text(text: str, client=None) -> list[ExtractedStartup]:
    client = client or anthropic.Anthropic()
    response = client.messages.parse(
        model=get_settings().anthropic_model,
        max_tokens=8192,
        messages=[{"role": "user",
                   "content": _EXTRACT_PROMPT.format(text=text[:MAX_INPUT_CHARS])}],
        output_format=StartupList,
    )
    if response.parsed_output is None:
        return []
    return response.parsed_output.startups
```

`app/scraper/sources/listicle.py`:

```python
import httpx
from bs4 import BeautifulSoup

from app.ai import extract_startups_from_text
from app.scraper.sources.base import StartupRecord

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")


class ListicleSource:
    name = "listicle"

    def fetch(self, limit: int = 100, url: str | None = None, **filters) -> list[StartupRecord]:
        if not url:
            raise ValueError("listicle source requires url=<article url>")
        resp = httpx.get(url, headers={"User-Agent": BROWSER_UA},
                         timeout=30, follow_redirects=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        text = " ".join(soup.get_text(separator=" ").split())
        extracted = extract_startups_from_text(text)
        return [
            StartupRecord(
                name=item.name,
                website=item.website,
                description=item.description or "",
                source=self.name,
            )
            for item in extracted[:limit]
        ]
```

Register `ListicleSource` in `app/scraper/sources/__init__.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_ai_extract.py tests/test_sources_listicle.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/ai.py app/scraper/sources/listicle.py app/scraper/sources/__init__.py tests/test_ai_extract.py tests/test_sources_listicle.py
git commit -m "feat: Claude structured extraction and listicle adapter"
```

---

### Task 10: CLI — init-db, discover, stats

**Files:**
- Create: `app/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything above
- Produces the `m2s` entry point with commands:
  - `m2s init-db` — create tables at the configured `db_path`
  - `m2s discover SOURCE [--limit N] [--region X] [--list-name X] [--topic X] [--url X] [--path X] [--no-profiles]` — run one adapter and ingest; prints `added/skipped`
  - `m2s stats` — counts of startups by status and by source
- Later phases add `m2s hunt`, `m2s draft`, `m2s send`, `m2s serve` to this same Typer app.

- [ ] **Step 1: Write the failing test**

`tests/test_cli.py`:

```python
from typer.testing import CliRunner

from app.cli import app
from app.scraper.sources.base import StartupRecord

runner = CliRunner()


def _use_tmp_db(monkeypatch, tmp_path):
    monkeypatch.setenv("M2S_DB_PATH", str(tmp_path / "test.db"))


def test_init_db(monkeypatch, tmp_path):
    _use_tmp_db(monkeypatch, tmp_path)
    result = runner.invoke(app, ["init-db"])
    assert result.exit_code == 0
    assert (tmp_path / "test.db").exists()


def test_discover_and_stats(monkeypatch, tmp_path):
    _use_tmp_db(monkeypatch, tmp_path)
    runner.invoke(app, ["init-db"])

    class FakeSource:
        name = "fake"
        def fetch(self, limit=100, **filters):
            return [StartupRecord(name="Acme", website="https://acme.com", source="fake")]

    import app.cli as cli_mod
    monkeypatch.setattr(cli_mod, "get_source", lambda name: FakeSource())

    result = runner.invoke(app, ["discover", "fake", "--limit", "5"])
    assert result.exit_code == 0
    assert "added=1" in result.output

    result = runner.invoke(app, ["stats"])
    assert result.exit_code == 0
    assert "discovered" in result.output and "fake" in result.output


def test_discover_unknown_source(monkeypatch, tmp_path):
    _use_tmp_db(monkeypatch, tmp_path)
    runner.invoke(app, ["init-db"])
    result = runner.invoke(app, ["discover", "nope"])
    assert result.exit_code != 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.cli'`

- [ ] **Step 3: Write minimal implementation**

`app/cli.py`:

```python
import typer
from sqlalchemy import func, select

from app.config import get_settings
from app.db import get_engine, init_db as _init_db, make_session
from app.models import Startup
from app.scraper.ingest import ingest_records
from app.scraper.sources import get_source

app = typer.Typer(help="Mail2Startups — automated internship outreach")


def _session():
    engine = get_engine(get_settings().db_path)
    _init_db(engine)
    return make_session(engine)


@app.command("init-db")
def init_db_cmd():
    """Create the database and all tables."""
    engine = get_engine(get_settings().db_path)
    _init_db(engine)
    typer.echo(f"Database ready at {get_settings().db_path}")


@app.command()
def discover(
    source: str = typer.Argument(..., help="csv | yc | startup_india | product_hunt | listicle"),
    limit: int = typer.Option(100, help="Max startups to fetch"),
    region: str = typer.Option(None, help="yc: region filter, e.g. 'india' or 'remote'"),
    list_name: str = typer.Option("hiring", help="yc: all | top | hiring"),
    topic: str = typer.Option(None, help="product_hunt: topic slug"),
    url: str = typer.Option(None, help="listicle: article URL"),
    path: str = typer.Option(None, help="csv: file path"),
    no_profiles: bool = typer.Option(False, help="startup_india: skip per-profile fetches"),
):
    """Fetch startups from a directory source and ingest them."""
    filters = {}
    if region: filters["region"] = region
    if list_name: filters["list_name"] = list_name
    if topic: filters["topic"] = topic
    if url: filters["url"] = url
    if path: filters["path"] = path
    if no_profiles: filters["fetch_profiles"] = False
    try:
        src = get_source(source)
        records = src.fetch(limit=limit, **filters)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    with _session() as session:
        result = ingest_records(session, records)
    typer.echo(f"{source}: fetched={len(records)} added={result.added} skipped={result.skipped}")


@app.command()
def stats():
    """Show startup counts by status and source."""
    with _session() as session:
        by_status = session.execute(
            select(Startup.status, func.count()).group_by(Startup.status)).all()
        by_source = session.execute(
            select(Startup.source, func.count()).group_by(Startup.source)).all()
    typer.echo("By status:")
    for status, count in by_status:
        typer.echo(f"  {status.value}: {count}")
    typer.echo("By source:")
    for source, count in by_source:
        typer.echo(f"  {source}: {count}")


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_cli.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python -m pytest -v`
Expected: ALL PASS (~23 tests)

- [ ] **Step 6: Commit**

```bash
git add app/cli.py tests/test_cli.py
git commit -m "feat: m2s CLI with init-db, discover, stats"
```

---

### Task 11: Live smoke test and phase wrap-up

**Files:**
- Create: `README.md`

**Interfaces:**
- Consumes: the full phase-1 build
- Produces: a verified working discovery pipeline and a README documenting setup + usage.

- [ ] **Step 1: Manual smoke test against the real YC endpoint** (the one deliberate online step; not part of the pytest suite)

Run:
```bash
.venv/Scripts/python -m app.cli init-db
.venv/Scripts/python -m app.cli discover yc --limit 25 --region india
.venv/Scripts/python -m app.cli discover yc --limit 25 --region remote
.venv/Scripts/python -m app.cli stats
```
Expected: both discover runs print `added=N` with N > 0; stats shows startups with status `discovered`. If the yc-oss endpoint shape changed, fix the mapping constants in `app/scraper/sources/yc.py` and update the fixture in `tests/test_sources_yc.py` to match reality.

- [ ] **Step 2: Smoke test Startup India** (tolerate failure — government API; if it rejects requests, note it and move on, CSV/listicle cover India lists)

Run: `.venv/Scripts/python -m app.cli discover startup_india --limit 5`
Expected: `added>0`, or a clear HTTP error to investigate later.

- [ ] **Step 3: Write README.md**

```markdown
# Mail2Startups

Automated internship outreach: discover startups, hunt contact emails,
AI-tailor resumes and emails, drip-send via Hostinger, track replies.

**Status: Phase 1 (foundation + startup discovery) complete.**
Spec: `docs/superpowers/specs/2026-08-11-mail2startups-design.md`

## Setup

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
cp .env.example .env   # then fill in ANTHROPIC_API_KEY etc.
```

## Usage

```bash
m2s init-db
m2s discover yc --limit 100 --region india      # YC directory (via yc-oss mirror)
m2s discover yc --limit 100 --region remote
m2s discover startup_india --limit 50           # DPIIT-recognized Indian startups
m2s discover product_hunt --topic developer-tools   # needs M2S_PRODUCT_HUNT_TOKEN
m2s discover listicle --url "https://inc42.com/...top-startups..."  # Claude extracts names
m2s discover csv --path my_list.csv
m2s stats
```

## Tests

```bash
.venv/Scripts/python -m pytest
```
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: README with phase-1 setup and usage"
```

---

## Self-review notes

- **Spec coverage (phase 1 scope):** all five directory adapters (Task 5–9), dedup ingest (Task 4), full five-table schema as later-phase contract (Task 2), CLI (Task 10). Email hunting, enrichment, drafting, sending, dashboard are explicitly Phase 2–4 and get their own plans.
- **Endpoints were verified 2026-08-11:** yc-oss GitHub Pages API and Startup India noauth API confirmed live via web search. Constants isolated per adapter for easy correction.
- **Type consistency:** `get_source` returns instances; every adapter takes `fetch(limit=..., **filters)`; `ingest_records(session, records)` used identically in Task 4 tests and Task 10 CLI.
