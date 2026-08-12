# Phase 5 — Inbox Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A one-shot `m2s inbox` command that polls the Hostinger IMAP mailbox read-only, records replies (Claude-classified) and bounces to a dedicated `inbox_messages` table, advances startup status, and sweeps stale sends to `no_response`.

**Architecture:** New `app/inbox/` module — `imap_client.py` (injected IMAP Protocol + real `HostingerImap` + pure `parse_message`), `matching.py` (pure `match_reply`/`detect_bounce`), `classify.py` (injected-Claude `classify_reply`), `service.py` (`poll_inbox` orchestrator). One new model (`InboxMessage`) + two enums + two `CampaignState` columns + six config fields + one CLI command. Detection is pure: a bounce is terminal (no re-target). Matches Phase 4's one-shot CLI + OS-scheduler + injected-client-for-offline-tests discipline exactly.

**Tech Stack:** Python 3.12+, SQLAlchemy 2.x + SQLite, pydantic-settings (`env_prefix="M2S_"`), Typer, `imaplib` (stdlib), `anthropic` SDK, stdlib `email`.

## Global Constraints

- **Fully offline tests:** no test opens a socket or calls a live API/IMAP/SMTP/DNS. The IMAP client and the Claude classifier are always injected; tests use fakes. Run: `.venv/Scripts/python -m pytest`.
- **Error containment — never bare `except`:** IMAP/network → `(imaplib.IMAP4.error, OSError)`; Claude classify → `(anthropic.AnthropicError, ValueError)` falling back to `ReplyLabel.OTHER`.
- **No new dependency:** `imaplib` is stdlib; `anthropic` already declared. No migration tooling — `init_db` runs `create_all` on a re-creatable local DB.
- **Read-only mailbox:** never set `\Seen`; incremental fetch by IMAP UID above a `CampaignState` watermark; UIDVALIDITY change resets the watermark.
- **Idempotent + status-gated:** dedup on the inbound email's own Message-ID (`inbox_messages.imap_message_id` UNIQUE); never re-advance a startup already `replied`/`bounced`.
- **Deterministic:** `poll_inbox` takes an injected `now`; the `no_response` cutoff is a pure function of `now` + DB state. No wall-clock read inside the service.
- **SQLite tz-naive gotcha:** `DateTime(timezone=True)` round-trips tz-NAIVE from SQLite. Pin a DB-read datetime to UTC with `.replace(tzinfo=timezone.utc)` before comparing to an aware `now` (helper `_as_utc`) — otherwise `.astimezone()`/comparisons misread it as host-local.
- **Secrets stay out of git:** IMAP creds live in `.env` (gitignored); `.env.example` holds blanks. Blank IMAP creds fall back to the SMTP creds.
- **Commit trailer:** `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Enum column pattern:** every `Enum(...)` column uses `values_callable=lambda e: [m.value for m in e]` (matches every existing model).

---

### Task 1: Models, enums, `CampaignState` columns, config

**Files:**
- Modify: `app/models.py` (add two enums + `InboxMessage` model + two `CampaignState` columns)
- Modify: `app/config.py` (add six IMAP/inbox settings)
- Test: `tests/test_inbox_models.py` (create)

**Interfaces:**
- Consumes: existing `Base`, `utcnow`, `Startup`, `Message`, `CampaignState`, and the imports already present in `app/models.py` (`DateTime, Enum, ForeignKey, Integer, String, Text`).
- Produces:
  - `app.models.InboxKind(str, enum.Enum)`: `REPLY = "reply"`, `BOUNCE = "bounce"`.
  - `app.models.ReplyLabel(str, enum.Enum)`: `INTERESTED = "interested"`, `REJECTION = "rejection"`, `AUTO_REPLY = "auto_reply"`, `OTHER = "other"`.
  - `app.models.InboxMessage` (table `inbox_messages`) with columns: `id: int` PK, `startup_id: int` FK→startups.id, `message_id: int | None` FK→messages.id, `kind: InboxKind`, `imap_message_id: str` UNIQUE, `imap_uid: int`, `from_addr: str`, `subject: str`, `snippet: str`, `label: ReplyLabel | None`, `matched_message_id: str | None`, `received_at: datetime | None`, `created_at: datetime`.
  - `CampaignState.last_imap_uid: int` (default 0), `CampaignState.imap_uidvalidity: int` (default 0).
  - `Settings` new fields: `imap_host: str = "imap.hostinger.com"`, `imap_port: int = 993`, `imap_user: str = ""`, `imap_password: str = ""`, `imap_mailbox: str = "INBOX"`, `no_response_days: int = 14`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_inbox_models.py`:

```python
from datetime import datetime, timezone

from app.config import get_settings
from app.db import get_engine, init_db, make_session
from app.models import (
    CampaignState, InboxKind, InboxMessage, ReplyLabel, Startup, StartupStatus,
)


def _session():
    engine = get_engine(":memory:")
    init_db(engine)
    return make_session(engine)


def test_enum_values():
    assert [k.value for k in InboxKind] == ["reply", "bounce"]
    assert [l.value for l in ReplyLabel] == [
        "interested", "rejection", "auto_reply", "other"]


def test_inbox_message_roundtrip():
    with _session() as s:
        startup = Startup(name="A", domain="a.io", source="yc",
                          status=StartupStatus.SENT)
        s.add(startup); s.commit()
        im = InboxMessage(
            startup_id=startup.id, message_id=None, kind=InboxKind.REPLY,
            imap_message_id="<abc@x>", imap_uid=42, from_addr="c@a.io",
            subject="Re: hi", snippet="hello", label=ReplyLabel.INTERESTED,
            matched_message_id="<out@d.com>",
            received_at=datetime(2026, 8, 12, tzinfo=timezone.utc))
        s.add(im); s.commit()
        got = s.get(InboxMessage, im.id)
        assert got.kind is InboxKind.REPLY
        assert got.label is ReplyLabel.INTERESTED
        assert got.imap_message_id == "<abc@x>" and got.imap_uid == 42
        assert got.label is not None and got.created_at is not None


def test_campaign_state_uid_watermark_defaults():
    with _session() as s:
        st = CampaignState(id=1)
        s.add(st); s.commit()
        assert st.last_imap_uid == 0 and st.imap_uidvalidity == 0


def test_settings_inbox_defaults():
    s = get_settings()
    assert s.imap_host == "imap.hostinger.com"
    assert s.imap_port == 993
    assert s.imap_mailbox == "INBOX"
    assert s.no_response_days == 14
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_inbox_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'InboxKind'`.

- [ ] **Step 3: Implement the models**

In `app/models.py`, add the two enums after the existing `MessageStatus` enum (before `class Startup`):

```python
class InboxKind(str, enum.Enum):
    REPLY = "reply"
    BOUNCE = "bounce"


class ReplyLabel(str, enum.Enum):
    INTERESTED = "interested"
    REJECTION = "rejection"
    AUTO_REPLY = "auto_reply"
    OTHER = "other"
```

Add the `InboxMessage` model after the existing `Event` class:

```python
class InboxMessage(Base):
    __tablename__ = "inbox_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    startup_id: Mapped[int] = mapped_column(ForeignKey("startups.id"))
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id"), nullable=True)
    kind: Mapped[InboxKind] = mapped_column(
        Enum(InboxKind, values_callable=lambda e: [m.value for m in e]))
    imap_message_id: Mapped[str] = mapped_column(String(255), unique=True)
    imap_uid: Mapped[int] = mapped_column(Integer, default=0)
    from_addr: Mapped[str] = mapped_column(String(255), default="")
    subject: Mapped[str] = mapped_column(String(500), default="")
    snippet: Mapped[str] = mapped_column(Text, default="")
    label: Mapped[ReplyLabel | None] = mapped_column(
        Enum(ReplyLabel, values_callable=lambda e: [m.value for m in e]),
        nullable=True)
    matched_message_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow)
```

Add the two watermark columns to the end of `class CampaignState`:

```python
    last_imap_uid: Mapped[int] = mapped_column(Integer, default=0)
    imap_uidvalidity: Mapped[int] = mapped_column(Integer, default=0)
```

- [ ] **Step 4: Implement the config fields**

In `app/config.py`, add these fields to `class Settings` after the `dkim_selector` line:

```python
    # IMAP / inbox (Phase 5) — Hostinger mailbox; blank IMAP creds fall back to SMTP
    imap_host: str = "imap.hostinger.com"
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""
    imap_mailbox: str = "INBOX"
    no_response_days: int = 14
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_inbox_models.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Run the full suite (nothing else regresses)**

Run: `.venv/Scripts/python -m pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/models.py app/config.py tests/test_inbox_models.py
git commit -m "feat: InboxMessage model, inbox enums, UID watermark + IMAP config

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `imap_client.py` — `FetchedMessage`, `parse_message`, Protocol, `HostingerImap`

**Files:**
- Create: `app/inbox/__init__.py` (empty)
- Create: `app/inbox/imap_client.py`
- Test: `tests/test_imap_client.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks (stdlib `email` only).
- Produces:
  - `FetchedMessage` dataclass: `uid: int`, `imap_message_id: str`, `from_addr: str`, `subject: str`, `in_reply_to: str`, `references: list[str]`, `body_text: str`, `raw: str`, `received_at: datetime | None`.
  - `parse_message(uid: int, raw_bytes: bytes) -> FetchedMessage` — pure; `imap_message_id` falls back to `f"uid:{uid}"` when the Message-ID header is absent.
  - `ImapClient` Protocol: `fetch_new(self, mailbox: str, since_uid: int, uidvalidity: int) -> tuple[int, list[FetchedMessage]]`.
  - `HostingerImap(host, port, user, password)` implementing `ImapClient` (real `IMAP4_SSL`; not exercised in tests).

- [ ] **Step 1: Write the failing test**

Create `tests/test_imap_client.py`:

```python
from app.inbox.imap_client import FetchedMessage, parse_message

RAW_REPLY = b"""From: Priya Nair <priya@globex.io>
To: me@d.com
Subject: Re: Internship application
Message-ID: <reply-1@globex.io>
In-Reply-To: <out-42@d.com>
References: <out-42@d.com>
Date: Wed, 12 Aug 2026 10:30:00 +0000
Content-Type: text/plain; charset="utf-8"

Thanks for reaching out! Happy to chat next week.
"""

RAW_NO_MSGID = b"""From: nobody@x.io
Subject: hi
Date: Wed, 12 Aug 2026 10:30:00 +0000
Content-Type: text/plain

body here
"""


def test_parse_message_extracts_headers_and_body():
    fm = parse_message(42, RAW_REPLY)
    assert isinstance(fm, FetchedMessage)
    assert fm.uid == 42
    assert fm.imap_message_id == "<reply-1@globex.io>"
    assert fm.from_addr == "priya@globex.io"
    assert fm.subject == "Re: Internship application"
    assert fm.in_reply_to == "<out-42@d.com>"
    assert fm.references == ["<out-42@d.com>"]
    assert "Happy to chat" in fm.body_text
    assert fm.received_at is not None and fm.received_at.year == 2026


def test_parse_message_falls_back_to_uid_when_no_message_id():
    fm = parse_message(7, RAW_NO_MSGID)
    assert fm.imap_message_id == "uid:7"
    assert fm.from_addr == "nobody@x.io"
    assert "body here" in fm.body_text
    assert fm.in_reply_to == "" and fm.references == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_imap_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.inbox'`.

- [ ] **Step 3: Create the empty package marker**

Create `app/inbox/__init__.py` (empty file, zero bytes).

- [ ] **Step 4: Implement `imap_client.py`**

Create `app/inbox/imap_client.py`:

```python
import imaplib
from dataclasses import dataclass
from datetime import datetime
from email import message_from_bytes, policy
from email.utils import parseaddr, parsedate_to_datetime
from typing import Protocol


@dataclass
class FetchedMessage:
    uid: int
    imap_message_id: str          # inbound Message-ID header; f"uid:{uid}" if absent
    from_addr: str
    subject: str
    in_reply_to: str
    references: list[str]
    body_text: str
    raw: str
    received_at: datetime | None


def _body_text(msg) -> str:
    """First text/plain part (skipping attachments), decoded to str."""
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.get_content_type() != "text/plain":
            continue
        if part.get_content_disposition() == "attachment":
            continue
        try:
            return part.get_content().strip()
        except (LookupError, ValueError):
            payload = part.get_payload(decode=True) or b""
            return payload.decode("utf-8", "ignore").strip()
    return ""


def parse_message(uid: int, raw_bytes: bytes) -> FetchedMessage:
    msg = message_from_bytes(raw_bytes, policy=policy.default)
    message_id = str(msg["Message-ID"] or "").strip()
    references = str(msg["References"] or "").split()
    try:
        received = parsedate_to_datetime(msg["Date"]) if msg["Date"] else None
    except (TypeError, ValueError):
        received = None
    return FetchedMessage(
        uid=uid,
        imap_message_id=message_id or f"uid:{uid}",
        from_addr=parseaddr(str(msg["From"] or ""))[1].lower(),
        subject=str(msg["Subject"] or "").strip(),
        in_reply_to=str(msg["In-Reply-To"] or "").strip(),
        references=references,
        body_text=_body_text(msg),
        raw=raw_bytes.decode("utf-8", "ignore"),
        received_at=received,
    )


class ImapClient(Protocol):
    def fetch_new(self, mailbox: str, since_uid: int,
                  uidvalidity: int) -> tuple[int, list[FetchedMessage]]:
        ...


class HostingerImap:
    """Real IMAP4_SSL client. Not exercised in tests (they inject a fake).

    Opens the mailbox read-only (never sets \\Seen). On a UIDVALIDITY mismatch it
    ignores `since_uid` and returns everything (watermark reset). IMAP/network
    failures raise (imaplib.IMAP4.error, OSError) for the CLI to contain."""

    def __init__(self, host: str, port: int, user: str, password: str):
        self._host = host
        self._port = port
        self._user = user
        self._password = password

    def fetch_new(self, mailbox: str, since_uid: int,
                  uidvalidity: int) -> tuple[int, list[FetchedMessage]]:
        conn = imaplib.IMAP4_SSL(self._host, self._port)
        try:
            conn.login(self._user, self._password)
            conn.select(mailbox, readonly=True)
            raw_validity = conn.response("UIDVALIDITY")[1][0]
            server_uidvalidity = int(raw_validity) if raw_validity else 0
            start = since_uid + 1 if server_uidvalidity == uidvalidity else 1
            _, data = conn.uid("search", None, f"UID {start}:*")
            uids = [int(x) for x in (data[0] or b"").split()]
            # "UID n:*" returns the highest UID even when it is below n; filter.
            uids = [u for u in uids if u >= start]
            messages: list[FetchedMessage] = []
            for u in uids:
                _, msg_data = conn.uid("fetch", str(u), "(RFC822)")
                if not msg_data or not isinstance(msg_data[0], tuple):
                    continue
                messages.append(parse_message(u, msg_data[0][1]))
            return server_uidvalidity, messages
        finally:
            try:
                conn.logout()
            except (imaplib.IMAP4.error, OSError):
                pass
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_imap_client.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add app/inbox/__init__.py app/inbox/imap_client.py tests/test_imap_client.py
git commit -m "feat: IMAP client — FetchedMessage, parse_message, HostingerImap

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `matching.py` — pure `match_reply` / `detect_bounce`

**Files:**
- Create: `app/inbox/matching.py`
- Test: `tests/test_matching.py` (create)

**Interfaces:**
- Consumes: duck-typed `fetched` objects exposing `.in_reply_to: str`, `.references: list[str]`, `.from_addr: str`, `.raw: str` (satisfied by `FetchedMessage` and by `SimpleNamespace` in tests).
- Produces:
  - `match_reply(fetched, *, sent_by_message_id: dict[str, tuple[int, int]], contact_emails_by_startup: dict[str, int]) -> tuple[int, int | None, str | None] | None` — returns `(startup_id, message_id, matched_smtp_id)`; the from-address fallback returns `(startup_id, None, None)`.
  - `detect_bounce(fetched, *, sent_by_message_id: dict[str, tuple[int, int]]) -> tuple[int, int] | None` — returns `(startup_id, message_id)`.
  - Both maps key on a stored `Message.smtp_message_id` → `(startup_id, message_id)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_matching.py`:

```python
from types import SimpleNamespace

from app.inbox.matching import detect_bounce, match_reply


def _fm(**over):
    base = dict(in_reply_to="", references=[], from_addr="", raw="")
    base.update(over)
    return SimpleNamespace(**base)


SENT = {"<out-1@d.com>": (10, 100), "<out-2@d.com>": (20, 200)}
CONTACTS = {"founder@acme.io": 30}


def test_match_reply_exact_in_reply_to():
    fm = _fm(in_reply_to="<out-1@d.com>", from_addr="x@acme.io")
    assert match_reply(fm, sent_by_message_id=SENT,
                       contact_emails_by_startup=CONTACTS) == (10, 100, "<out-1@d.com>")


def test_match_reply_references_only():
    fm = _fm(references=["<other@z>", "<out-2@d.com>"], from_addr="x@z")
    assert match_reply(fm, sent_by_message_id=SENT,
                       contact_emails_by_startup=CONTACTS) == (20, 200, "<out-2@d.com>")


def test_match_reply_from_address_fallback():
    fm = _fm(from_addr="Founder@Acme.io")
    assert match_reply(fm, sent_by_message_id=SENT,
                       contact_emails_by_startup=CONTACTS) == (30, None, None)


def test_match_reply_no_match_returns_none():
    fm = _fm(from_addr="stranger@nowhere.io")
    assert match_reply(fm, sent_by_message_id=SENT,
                       contact_emails_by_startup=CONTACTS) is None


def test_detect_bounce_mailer_daemon_known_id():
    fm = _fm(from_addr="MAILER-DAEMON@d.com",
             raw="Delivery failed for <out-1@d.com> ... 550 no such user")
    assert detect_bounce(fm, sent_by_message_id=SENT) == (10, 100)


def test_detect_bounce_dsn_body_marker_known_id():
    fm = _fm(from_addr="postmaster@relay.io",
             raw="Content-Type: message/delivery-status\nfailed <out-2@d.com>")
    assert detect_bounce(fm, sent_by_message_id=SENT) == (20, 200)


def test_detect_bounce_ignores_ordinary_mail():
    fm = _fm(from_addr="friend@d.com", raw="hi how are you <out-1@d.com>")
    assert detect_bounce(fm, sent_by_message_id=SENT) is None


def test_detect_bounce_unknown_id_returns_none():
    fm = _fm(from_addr="mailer-daemon@d.com",
             raw="Delivery failed for <unknown@d.com>")
    assert detect_bounce(fm, sent_by_message_id=SENT) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_matching.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.inbox.matching'`.

- [ ] **Step 3: Implement `matching.py`**

Create `app/inbox/matching.py`:

```python
_BOUNCE_SENDERS = ("mailer-daemon", "postmaster")
_DSN_MARKERS = (
    "message/delivery-status",
    "report-type=delivery-status",
    "delivery status notification",
    "undelivered mail returned",
)


def match_reply(fetched, *, sent_by_message_id, contact_emails_by_startup):
    """Exact first: any id in In-Reply-To/References that is a key of
    sent_by_message_id → (startup_id, message_id, matched_smtp_id). Fallback:
    from_addr matches a contact of a startup with a SENT message →
    (startup_id, None, None). Else None."""
    for candidate in (fetched.in_reply_to, *fetched.references):
        mid = candidate.strip()
        if mid and mid in sent_by_message_id:
            startup_id, message_id = sent_by_message_id[mid]
            return startup_id, message_id, mid
    startup_id = contact_emails_by_startup.get(fetched.from_addr.lower())
    if startup_id is not None:
        return startup_id, None, None
    return None


def detect_bounce(fetched, *, sent_by_message_id):
    """A DSN/MAILER-DAEMON envelope (mailer-daemon/postmaster sender, or a
    delivery-status marker in the raw source) whose raw body carries one of our
    smtp_message_ids → (startup_id, message_id). Else None."""
    frm = fetched.from_addr.lower()
    raw_lower = fetched.raw.lower()
    is_dsn = (any(tok in frm for tok in _BOUNCE_SENDERS)
              or any(marker in raw_lower for marker in _DSN_MARKERS))
    if not is_dsn:
        return None
    for mid, (startup_id, message_id) in sent_by_message_id.items():
        if mid and mid in fetched.raw:
            return startup_id, message_id
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_matching.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add app/inbox/matching.py tests/test_matching.py
git commit -m "feat: reply/bounce matching (exact-first, DSN detection)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: `classify.py` — injected-Claude `classify_reply`

**Files:**
- Create: `app/inbox/classify.py`
- Test: `tests/test_classify.py` (create)

**Interfaces:**
- Consumes: `app.config.get_settings` (for the default model), `app.models.ReplyLabel`.
- Produces: `classify_reply(client, text: str, *, model: str | None = None) -> ReplyLabel`. Calls `client.messages.create(model=..., max_tokens=16, messages=[...])` and reads `resp.content[0].text`. Contains `(anthropic.AnthropicError, ValueError)` → `ReplyLabel.OTHER`; an unrecognised label string → `ReplyLabel.OTHER`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_classify.py`:

```python
import anthropic

from app.inbox.classify import classify_reply
from app.models import ReplyLabel


class _Content:
    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, text):
        self.content = [_Content(text)]


class _Messages:
    def __init__(self, text=None, exc=None):
        self._text, self._exc = text, exc
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        return _Resp(self._text)


class _Client:
    def __init__(self, text=None, exc=None):
        self.messages = _Messages(text, exc)


def test_classify_maps_valid_label():
    assert classify_reply(_Client(text="interested"), "yes let's talk",
                          model="m") is ReplyLabel.INTERESTED
    assert classify_reply(_Client(text="rejection"), "no thanks",
                          model="m") is ReplyLabel.REJECTION
    assert classify_reply(_Client(text="auto-reply"), "out of office",
                          model="m") is ReplyLabel.AUTO_REPLY


def test_classify_unknown_label_falls_back_to_other():
    assert classify_reply(_Client(text="banana"), "hmm", model="m") is ReplyLabel.OTHER


def test_classify_error_falls_back_to_other():
    client = _Client(exc=anthropic.AnthropicError("boom"))
    assert classify_reply(client, "anything", model="m") is ReplyLabel.OTHER
    assert client.messages.calls == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_classify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.inbox.classify'`.

- [ ] **Step 3: Implement `classify.py`**

Create `app/inbox/classify.py`:

```python
import anthropic

from app.config import get_settings
from app.models import ReplyLabel

MAX_TOKENS = 16

_PROMPT = (
    "Classify this reply to a cold internship-outreach email into exactly one "
    "label:\n"
    "  interested — the recipient is open, wants to talk, or asks for a call or "
    "more info.\n"
    "  rejection — a clear no: not hiring, not a fit, or a decline.\n"
    "  auto_reply — an automated out-of-office / autoresponder / no-reply notice.\n"
    "  other — anything else.\n"
    "Reply with ONLY the single label word.\n\n"
    "EMAIL:\n{text}"
)

# Ordered substring probes — tolerant of the model's punctuation/casing
# ("auto_reply" vs "auto-reply" vs "auto reply").
_KEYWORDS = (
    ("interest", ReplyLabel.INTERESTED),
    ("reject", ReplyLabel.REJECTION),
    ("auto", ReplyLabel.AUTO_REPLY),
)


def classify_reply(client, text: str, *, model: str | None = None) -> ReplyLabel:
    model = model or get_settings().anthropic_model
    prompt = _PROMPT.format(text=(text or "")[:4000])
    try:
        resp = client.messages.create(
            model=model, max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}])
        raw = resp.content[0].text.strip().lower()
    except (anthropic.AnthropicError, ValueError):
        return ReplyLabel.OTHER
    for needle, label in _KEYWORDS:
        if needle in raw:
            return label
    return ReplyLabel.OTHER
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_classify.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add app/inbox/classify.py tests/test_classify.py
git commit -m "feat: Claude reply classifier (injected client, OTHER fallback)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: `service.py` — `poll_inbox` orchestrator

**Files:**
- Create: `app/inbox/service.py`
- Test: `tests/test_inbox_service.py` (create)

**Interfaces:**
- Consumes: `match_reply`, `detect_bounce` (Task 3); `FetchedMessage` (Task 2, for the test's fakes); models `Contact, Draft, Event, InboxKind, InboxMessage, Message, MessageStatus, ReplyLabel, Startup, StartupStatus` (Task 1); `app.send.state.ensure_state`; an injected `imap` with `fetch_new(mailbox, since_uid, uidvalidity) -> (uidvalidity, list[FetchedMessage])` and an injected `classifier(text) -> ReplyLabel`.
- Produces:
  - `InboxResult` dataclass: `replies: int`, `bounces: int`, `no_response: int`, `fetched: int`.
  - `poll_inbox(session, *, imap, classifier, now, settings, limit: int | None = None, dry_run: bool = False) -> InboxResult`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_inbox_service.py`:

```python
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import select

from app.inbox.imap_client import FetchedMessage
from app.inbox.service import poll_inbox
from app.models import (
    Contact, Draft, DraftStatus, Event, InboxKind, InboxMessage, Message,
    MessageStatus, MessageType, ReplyLabel, Startup, StartupStatus,
)
from app.send.state import ensure_state

NOW = datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc)


def _settings(**over):
    base = dict(imap_mailbox="INBOX", no_response_days=14)
    base.update(over)
    return SimpleNamespace(**base)


def _interested(text):
    return ReplyLabel.INTERESTED


def _seed_sent(session, name, domain, smtp_id, *, sent_at, verified=True):
    s = Startup(name=name, domain=domain, source="yc", status=StartupStatus.SENT)
    session.add(s); session.commit()
    c = Contact(startup_id=s.id, name="C", role="CTO", email=f"c@{domain}",
                found_via="scraped", confidence=0.9, verified=verified)
    session.add(c); session.commit()
    d = Draft(startup_id=s.id, contact_id=c.id, subject="Hi", body="Hello",
              resume_pdf_path=None, status=DraftStatus.APPROVED)
    session.add(d); session.commit()
    m = Message(draft_id=d.id, type=MessageType.INITIAL, sent_at=sent_at,
                smtp_message_id=smtp_id, status=MessageStatus.SENT)
    session.add(m); session.commit()
    return s, c, d, m


def _fetched(uid, message_id, *, from_addr="", subject="", in_reply_to="",
             references=None, body_text="", raw="", received_at=None):
    return FetchedMessage(
        uid=uid, imap_message_id=message_id, from_addr=from_addr, subject=subject,
        in_reply_to=in_reply_to, references=references or [], body_text=body_text,
        raw=raw, received_at=received_at)


class FakeImap:
    def __init__(self, messages, uidvalidity=1):
        self._messages, self._uidvalidity = messages, uidvalidity

    def fetch_new(self, mailbox, since_uid, uidvalidity):
        return self._uidvalidity, list(self._messages)


def test_reply_advances_startup_and_records(session):
    s, c, d, m = _seed_sent(session, "A", "a.io", "<out-1@d.com>", sent_at=NOW)
    fm = _fetched(10, "<in-1@a.io>", from_addr="c@a.io", subject="Re: Hi",
                  in_reply_to="<out-1@d.com>", body_text="Sounds great!")
    res = poll_inbox(session, imap=FakeImap([fm]), classifier=_interested,
                     now=NOW, settings=_settings())
    assert res.replies == 1 and res.bounces == 0 and res.fetched == 1
    session.refresh(s); session.refresh(m)
    assert s.status == StartupStatus.REPLIED
    assert m.status == MessageStatus.REPLIED
    im = session.scalars(select(InboxMessage)).one()
    assert im.kind == InboxKind.REPLY and im.label == ReplyLabel.INTERESTED
    assert im.matched_message_id == "<out-1@d.com>"
    ev = session.scalars(select(Event).where(Event.kind == "reply")).one()
    assert ev.payload["label"] == "interested"


def test_bounce_marks_terminal_and_demotes_contact(session):
    s, c, d, m = _seed_sent(session, "B", "b.io", "<out-2@d.com>", sent_at=NOW)
    fm = _fetched(11, "<dsn-1@mail>", from_addr="mailer-daemon@b.io",
                  subject="Undelivered Mail Returned to Sender",
                  raw="550 user unknown; original message <out-2@d.com> failed",
                  body_text="delivery failed")
    res = poll_inbox(session, imap=FakeImap([fm]), classifier=_interested,
                     now=NOW, settings=_settings())
    assert res.bounces == 1 and res.replies == 0
    session.refresh(s); session.refresh(c); session.refresh(m)
    assert s.status == StartupStatus.BOUNCED
    assert m.status == MessageStatus.BOUNCED
    assert c.verified is False
    im = session.scalars(select(InboxMessage)).one()
    assert im.kind == InboxKind.BOUNCE and im.label is None
    assert session.scalars(select(Event).where(Event.kind == "bounce")).one()


def test_no_response_sweeps_aged_and_leaves_fresh(session):
    old_s, *_ = _seed_sent(session, "Old", "old.io", "<out-old@d.com>",
                           sent_at=NOW - timedelta(days=20))
    fresh_s, *_ = _seed_sent(session, "New", "new.io", "<out-new@d.com>",
                             sent_at=NOW - timedelta(days=3))
    res = poll_inbox(session, imap=FakeImap([]), classifier=_interested,
                     now=NOW, settings=_settings())
    assert res.no_response == 1
    session.refresh(old_s); session.refresh(fresh_s)
    assert old_s.status == StartupStatus.NO_RESPONSE
    assert fresh_s.status == StartupStatus.SENT
    assert session.scalars(
        select(Event).where(Event.kind == "no_response")).one()


def test_second_poll_is_noop(session):
    _seed_sent(session, "A", "a.io", "<out-1@d.com>", sent_at=NOW)
    fm = _fetched(10, "<in-1@a.io>", from_addr="c@a.io",
                  in_reply_to="<out-1@d.com>", body_text="hi")
    imap = FakeImap([fm])
    poll_inbox(session, imap=imap, classifier=_interested, now=NOW,
               settings=_settings())
    before = len(session.scalars(select(InboxMessage)).all())
    res2 = poll_inbox(session, imap=imap, classifier=_interested, now=NOW,
                      settings=_settings())
    after = len(session.scalars(select(InboxMessage)).all())
    assert res2.replies == 0 and before == after == 1


def test_dry_run_mutates_nothing(session):
    s, *_ = _seed_sent(session, "A", "a.io", "<out-1@d.com>", sent_at=NOW)
    fm = _fetched(10, "<in-1@a.io>", from_addr="c@a.io",
                  in_reply_to="<out-1@d.com>", body_text="hi")
    res = poll_inbox(session, imap=FakeImap([fm]), classifier=_interested,
                     now=NOW, settings=_settings(), dry_run=True)
    assert res.replies == 1                       # counted
    session.refresh(s)
    assert s.status == StartupStatus.SENT          # unchanged
    assert session.scalars(select(InboxMessage)).all() == []
    assert ensure_state(session).last_imap_uid == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_inbox_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.inbox.service'`.

- [ ] **Step 3: Implement `service.py`**

Create `app/inbox/service.py`:

```python
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.inbox.matching import detect_bounce, match_reply
from app.models import (
    Contact, Draft, Event, InboxKind, InboxMessage, Message, MessageStatus,
    ReplyLabel, Startup, StartupStatus,
)
from app.send import state as state_mod


@dataclass
class InboxResult:
    replies: int
    bounces: int
    no_response: int
    fetched: int


def _as_utc(dt: datetime) -> datetime:
    """SQLite round-trips DateTime(timezone=True) as tz-naive. A naive value read
    back is UTC (we always store UTC), so pin it before comparing to an aware now
    — otherwise the comparison misreads it as host-local time."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _sent_maps(session: Session):
    """Build (sent_by_message_id, contact_emails_by_startup) from the DB."""
    rows = session.execute(
        select(Message.smtp_message_id, Message.id, Draft.startup_id)
        .join(Draft, Message.draft_id == Draft.id)
        .where(Message.smtp_message_id.is_not(None))
    ).all()
    sent_by_message_id = {smtp: (sid, mid) for smtp, mid, sid in rows}
    sent_startup_ids = {sid for _, _, sid in rows}
    contact_rows = session.execute(
        select(Contact.email, Contact.startup_id)
        .where(Contact.startup_id.in_(sent_startup_ids),
               Contact.email.is_not(None))
    ).all()
    contact_emails_by_startup = {
        email.lower(): sid for email, sid in contact_rows if email}
    return sent_by_message_id, contact_emails_by_startup


def _newest_sent_at(session: Session, startup_id: int):
    return session.scalar(
        select(func.max(Message.sent_at))
        .join(Draft, Message.draft_id == Draft.id)
        .where(Draft.startup_id == startup_id,
               Message.status == MessageStatus.SENT))


def _record_bounce(session, fetched, startup_id, message_id):
    matched_smtp = None
    msg = session.get(Message, message_id) if message_id else None
    if msg is not None:
        msg.status = MessageStatus.BOUNCED
        matched_smtp = msg.smtp_message_id
        draft = session.get(Draft, msg.draft_id)
        if draft is not None and draft.contact_id is not None:
            contact = session.get(Contact, draft.contact_id)
            if contact is not None:
                contact.verified = False
    startup = session.get(Startup, startup_id)
    if startup is not None:
        startup.status = StartupStatus.BOUNCED
    im = InboxMessage(
        startup_id=startup_id, message_id=message_id, kind=InboxKind.BOUNCE,
        imap_message_id=fetched.imap_message_id, imap_uid=fetched.uid,
        from_addr=fetched.from_addr, subject=fetched.subject,
        snippet=fetched.body_text[:500], label=None,
        matched_message_id=matched_smtp, received_at=fetched.received_at)
    session.add(im)
    session.flush()
    session.add(Event(startup_id=startup_id, kind="bounce",
                      payload={"inbox_message_id": im.id, "message_id": message_id}))


def _record_reply(session, fetched, startup_id, message_id, matched, label):
    msg = session.get(Message, message_id) if message_id else None
    if msg is not None:
        msg.status = MessageStatus.REPLIED
    startup = session.get(Startup, startup_id)
    if startup is not None:
        startup.status = StartupStatus.REPLIED
    im = InboxMessage(
        startup_id=startup_id, message_id=message_id, kind=InboxKind.REPLY,
        imap_message_id=fetched.imap_message_id, imap_uid=fetched.uid,
        from_addr=fetched.from_addr, subject=fetched.subject,
        snippet=fetched.body_text[:500], label=label,
        matched_message_id=matched, received_at=fetched.received_at)
    session.add(im)
    session.flush()
    session.add(Event(startup_id=startup_id, kind="reply",
                      payload={"inbox_message_id": im.id, "message_id": message_id,
                               "label": label.value}))


def _sweep_no_response(session, now, no_response_days, *, mutate):
    cutoff = now - timedelta(days=no_response_days)
    due = []
    for s in session.scalars(
            select(Startup).where(Startup.status == StartupStatus.SENT)).all():
        newest = _newest_sent_at(session, s.id)
        if newest is None:
            continue
        if _as_utc(newest) < cutoff:
            due.append(s)
    if mutate:
        for s in due:
            s.status = StartupStatus.NO_RESPONSE
            session.add(Event(startup_id=s.id, kind="no_response", payload={}))
    return len(due)


def poll_inbox(session: Session, *, imap, classifier, now, settings,
               limit: int | None = None, dry_run: bool = False) -> InboxResult:
    state = state_mod.ensure_state(session)
    uidvalidity, msgs = imap.fetch_new(
        settings.imap_mailbox, state.last_imap_uid, state.imap_uidvalidity)
    if limit is not None:
        msgs = msgs[:limit]
    fetched = len(msgs)

    sent_by_message_id, contact_emails_by_startup = _sent_maps(session)
    replies = bounces = 0
    max_uid = state.last_imap_uid

    for fm in msgs:
        max_uid = max(max_uid, fm.uid)
        # Dedup on the inbound Message-ID (autoflush makes a same-batch add visible).
        if session.scalar(select(InboxMessage.id).where(
                InboxMessage.imap_message_id == fm.imap_message_id)) is not None:
            continue

        bounce = detect_bounce(fm, sent_by_message_id=sent_by_message_id)
        if bounce is not None:
            startup_id, message_id = bounce
            bounces += 1
            if not dry_run:
                _record_bounce(session, fm, startup_id, message_id)
            continue

        rep = match_reply(fm, sent_by_message_id=sent_by_message_id,
                          contact_emails_by_startup=contact_emails_by_startup)
        if rep is None:
            continue
        startup_id, message_id, matched = rep
        startup = session.get(Startup, startup_id)
        if startup is not None and startup.status in (
                StartupStatus.REPLIED, StartupStatus.BOUNCED):
            continue
        label = classifier(fm.body_text)
        replies += 1
        if not dry_run:
            _record_reply(session, fm, startup_id, message_id, matched, label)

    no_response = _sweep_no_response(
        session, now, settings.no_response_days, mutate=not dry_run)

    if dry_run:
        session.rollback()
    else:
        state.last_imap_uid = max_uid
        state.imap_uidvalidity = uidvalidity
        session.commit()
    return InboxResult(replies, bounces, no_response, fetched)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_inbox_service.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add app/inbox/service.py tests/test_inbox_service.py
git commit -m "feat: poll_inbox — record replies/bounces, no_response sweep

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: CLI `m2s inbox` + `.env.example` + README

**Files:**
- Modify: `app/cli.py` (imports + `_build_imap` + `_build_classifier` + `inbox` command)
- Modify: `.env.example` (append IMAP block)
- Modify: `README.md` (add "Inbox (Phase 5)" section before "## Tests")
- Test: `tests/test_cli_inbox.py` (create)

**Interfaces:**
- Consumes: `poll_inbox` (Task 5), `HostingerImap` (Task 2), `classify_reply` (Task 4), `resolve_backend` (`app.draft.claude_draft`), `get_settings`, `_session`.
- Produces (module-level in `app.cli`, monkeypatchable by tests): `_build_imap(settings) -> HostingerImap`, `_build_classifier(settings) -> Callable[[str], ReplyLabel]`, and the `inbox` Typer command. Command output line: `inbox: fetched=<n> replies=<n> bounces=<n> no_response=<n>`. Exits 1 with an `M2S_IMAP_USER`-mentioning message when no IMAP/SMTP credentials resolve.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_inbox.py`:

```python
from datetime import datetime, timezone

from sqlalchemy import select
from typer.testing import CliRunner

import app.cli as cli_mod
from app.cli import app
from app.db import get_engine, init_db, make_session
from app.inbox.imap_client import FetchedMessage
from app.models import (
    Contact, Draft, DraftStatus, InboxMessage, Message, MessageStatus,
    MessageType, ReplyLabel, Startup, StartupStatus,
)

runner = CliRunner()


def _prepare(monkeypatch, tmp_path):
    db = tmp_path / "test.db"
    monkeypatch.setenv("M2S_DB_PATH", str(db))
    monkeypatch.setenv("M2S_IMAP_USER", "me@d.com")
    monkeypatch.setenv("M2S_IMAP_PASSWORD", "secret")
    runner.invoke(app, ["init-db"])
    return db


def _seed_sent(db, smtp_id):
    engine = get_engine(db); init_db(engine)
    with make_session(engine) as s:
        st = Startup(name="A", domain="a.io", source="yc",
                     status=StartupStatus.SENT)
        s.add(st); s.commit()
        c = Contact(startup_id=st.id, name="C", role="CTO", email="c@a.io",
                    found_via="scraped", confidence=0.9, verified=True)
        s.add(c); s.commit()
        d = Draft(startup_id=st.id, contact_id=c.id, subject="Hi", body="Hello",
                  resume_pdf_path=None, status=DraftStatus.APPROVED)
        s.add(d); s.commit()
        m = Message(draft_id=d.id, type=MessageType.INITIAL,
                    sent_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
                    smtp_message_id=smtp_id, status=MessageStatus.SENT)
        s.add(m); s.commit()


class _FakeImap:
    def __init__(self, messages):
        self._messages = messages

    def fetch_new(self, mailbox, since_uid, uidvalidity):
        return 1, list(self._messages)


def test_inbox_records_reply(monkeypatch, tmp_path):
    db = _prepare(monkeypatch, tmp_path)
    _seed_sent(db, "<out-1@d.com>")
    fm = FetchedMessage(uid=10, imap_message_id="<in-1@a.io>", from_addr="c@a.io",
                        subject="Re: Hi", in_reply_to="<out-1@d.com>",
                        references=[], body_text="great!", raw="", received_at=None)
    monkeypatch.setattr(cli_mod, "_build_imap", lambda settings: _FakeImap([fm]))
    monkeypatch.setattr(cli_mod, "_build_classifier",
                        lambda settings: (lambda text: ReplyLabel.INTERESTED))
    out = runner.invoke(app, ["inbox"])
    assert out.exit_code == 0
    assert "fetched=1" in out.output and "replies=1" in out.output
    assert "bounces=0" in out.output
    with make_session(get_engine(db)) as s:
        assert s.scalars(select(Startup)).one().status == StartupStatus.REPLIED
        assert s.scalars(select(InboxMessage)).one().label == ReplyLabel.INTERESTED


def test_inbox_requires_credentials(monkeypatch, tmp_path):
    db = tmp_path / "t.db"
    monkeypatch.setenv("M2S_DB_PATH", str(db))
    monkeypatch.setenv("M2S_IMAP_USER", "")
    monkeypatch.setenv("M2S_IMAP_PASSWORD", "")
    monkeypatch.setenv("M2S_SMTP_USER", "")
    monkeypatch.setenv("M2S_SMTP_PASSWORD", "")
    runner.invoke(app, ["init-db"])
    out = runner.invoke(app, ["inbox"])
    assert out.exit_code == 1 and "M2S_IMAP_USER" in out.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_cli_inbox.py -v`
Expected: FAIL — `AttributeError: module 'app.cli' has no attribute '_build_imap'` (or a Typer "no such command 'inbox'" error).

- [ ] **Step 3: Add imports to `app/cli.py`**

Add these imports alongside the existing `app.send`/`app.draft` imports near the top of `app/cli.py`:

```python
from app.inbox.classify import classify_reply
from app.inbox.imap_client import HostingerImap
from app.inbox.service import poll_inbox
```

- [ ] **Step 4: Add the builders and the `inbox` command**

In `app/cli.py`, add these two builders next to the existing `_build_transport` (they must be module-level so tests can monkeypatch them):

```python
def _build_imap(settings):
    return HostingerImap(
        settings.imap_host, settings.imap_port,
        settings.imap_user or settings.smtp_user,
        settings.imap_password or settings.smtp_password)


def _build_classifier(settings):
    from app.draft.claude_draft import resolve_backend
    client, model = resolve_backend()
    return lambda text: classify_reply(client, text, model=model)
```

Add the command (place it after the `send` command, before `pause`):

```python
@app.command()
def inbox(
    limit: int = typer.Option(None, help="Max inbound messages to process this run"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Match + classify; change nothing"),
):
    """Poll the IMAP inbox once: record replies/bounces, sweep stale sends."""
    import imaplib
    settings = get_settings()
    user = settings.imap_user or settings.smtp_user
    password = settings.imap_password or settings.smtp_password
    if not user or not password:
        typer.echo("Error: set M2S_IMAP_USER/M2S_IMAP_PASSWORD "
                   "(or M2S_SMTP_USER/M2S_SMTP_PASSWORD) in .env", err=True)
        raise typer.Exit(code=1)
    imap = _build_imap(settings)
    try:
        classifier = _build_classifier(settings)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    now = datetime.now(timezone.utc)
    try:
        with _session() as session:
            result = poll_inbox(session, imap=imap, classifier=classifier,
                                now=now, settings=settings, limit=limit,
                                dry_run=dry_run)
    except (imaplib.IMAP4.error, OSError) as exc:
        typer.echo(f"Error: IMAP fetch failed: {exc}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"inbox: fetched={result.fetched} replies={result.replies} "
               f"bounces={result.bounces} no_response={result.no_response}")
```

- [ ] **Step 5: Run the CLI test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_cli_inbox.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Append the IMAP block to `.env.example`**

Append to the end of `.env.example`:

```
# IMAP / inbox (Phase 5) — Hostinger mailbox. Blank values fall back to the
# SMTP credentials above.
M2S_IMAP_HOST=imap.hostinger.com
M2S_IMAP_PORT=993
M2S_IMAP_USER=
M2S_IMAP_PASSWORD=
M2S_IMAP_MAILBOX=INBOX
M2S_NO_RESPONSE_DAYS=14
```

- [ ] **Step 7: Add the README section**

In `README.md`, insert this section immediately before the `## Tests` heading:

```markdown
## Inbox (Phase 5)

Close the read side of the loop: poll the mailbox, record replies and bounces,
and age out silent sends.

```bash
m2s inbox --dry-run          # match + classify inbound mail, change nothing
m2s inbox                    # record replies/bounces; sweep stale sends to no_response
m2s inbox --limit 50         # cap messages processed this run
```

`m2s inbox` fetches unseen mail read-only by IMAP UID (it never marks messages
read), matches each to a sent `Message` via `In-Reply-To`/`References` (with a
from-address fallback), and advances status:

- **reply** → startup `replied`, classified by Claude (interested / rejection /
  auto-reply / other), stored in `inbox_messages`.
- **bounce** (MAILER-DAEMON / DSN) → message `bounced`, the contact demoted, and
  the startup `bounced` (terminal in this phase — re-targeting is a later phase).
- **no reply after `M2S_NO_RESPONSE_DAYS`** (default 14) → startup `no_response`.

It is one-shot and idempotent — schedule it every ~10 min during the send window
(Windows Task Scheduler / cron). Set the IMAP block in `.env` (see
`.env.example`); blank IMAP creds fall back to the SMTP credentials.
```

- [ ] **Step 8: Run the full suite**

Run: `.venv/Scripts/python -m pytest -q`
Expected: PASS (all tests, no regressions).

- [ ] **Step 9: Commit**

```bash
git add app/cli.py .env.example README.md tests/test_cli_inbox.py
git commit -m "feat: m2s inbox CLI command + docs

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage** (each spec section → task):
- `app/inbox/` module + `__init__.py` → Tasks 2–5.
- `InboxMessage` model + `InboxKind`/`ReplyLabel` enums + `CampaignState` columns → Task 1.
- Config (six `M2S_` IMAP/inbox fields) → Task 1; `.env.example` → Task 6.
- `imap_client.py` (`FetchedMessage`, `parse_message`, `ImapClient` Protocol, `HostingerImap`, UIDVALIDITY reset) → Task 2.
- `matching.py` (`match_reply` exact-first + from-fallback, `detect_bounce` DSN) → Task 3.
- `classify.py` (injected Claude, `(anthropic.AnthropicError, ValueError)` → OTHER) → Task 4.
- `service.py` (`poll_inbox`, dedup, status gates, watermark advance, `no_response` sweep, dry-run) → Task 5.
- CLI `m2s inbox` (read-only, injected builders, error containment `(imaplib.IMAP4.error, OSError)`, missing-cred exit) → Task 6.
- README "Inbox (Phase 5)" → Task 6.
- Testing (matching, classify, service reply/bounce/sweep/idempotent/dry-run, CLI wiring + missing-creds) → Tasks 1–6; all offline.

**2. Placeholder scan:** No TBD/TODO/"add error handling"/"similar to Task N". Every code and test step carries full verbatim code.

**3. Type consistency:**
- `fetch_new(mailbox, since_uid, uidvalidity) -> (int, list[FetchedMessage])` — identical in Task 2 (Protocol + `HostingerImap`), Task 5 (`FakeImap` + `poll_inbox` call), Task 6 (`_FakeImap`). ✔
- `sent_by_message_id: {smtp_id: (startup_id, message_id)}` — built in Task 5 `_sent_maps`, consumed by Task 3 `match_reply`/`detect_bounce`. Return of `match_reply` is `(startup_id, message_id, matched_smtp_id)`; `detect_bounce` is `(startup_id, message_id)`. Consumed identically in `poll_inbox`. ✔
- `classifier(text) -> ReplyLabel` — injected in Task 5 tests (`_interested`) and Task 6 (`_build_classifier` closure over `classify_reply(client, text, *, model)`). ✔
- `InboxMessage` field names in Task 1 model match every write in Task 5 (`_record_bounce`/`_record_reply`) and every read in tests (`kind`, `label`, `imap_message_id`, `matched_message_id`). ✔
- `InboxResult(replies, bounces, no_response, fetched)` — defined Task 5, read in Task 5 tests and Task 6 CLI output. ✔
- `_as_utc` guard reused from the Phase 4 pacing lesson for the `no_response` comparison. ✔

**Ready for execution.**
