# Phase 4 — Sending Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `approved` drafts into real, drip-paced emails sent from the user's Hostinger mailbox, driven entirely from the CLI, with DNS preflight and test sends as deliverability rails.

**Architecture:** New `app/send/` module — `smtp_client` (MIME + injectable transport), `pacing` (pure window/cap/ramp functions), `state` (a singleton `CampaignState` row for pause + failure count + ramp anchor), `service` (approve/reject + one-shot `send_batch` + test sends), `preflight` (SPF/DKIM/DMARC via an injectable resolver). New CLI commands wire them. One-shot `m2s send` sends the next-due email(s) respecting pause → window → budget, then exits; pacing gaps live in the OS scheduler.

**Tech Stack:** Python 3.12, SQLAlchemy 2 + SQLite, Typer, `email`/`smtplib`/`ssl` stdlib, `zoneinfo` stdlib, `dnspython` (already declared and installed, v2.8.0), pytest.

**Parent spec:** `docs/superpowers/specs/2026-08-12-phase4-sending-design.md`

## Global Constraints

- **Fully offline tests.** No test opens a socket. The SMTP transport and the DNS resolver are injected; tests pass recording/fake objects. Never construct `SmtpTransport` or call `dns.resolver` in a test.
- **No bare `except`.** SMTP/network containment is exactly `(smtplib.SMTPException, OSError)`. The DNS resolver wrapper catches the specific `dns.exception.DNSException`. Preflight core logic has no try/except (the resolver returns `[]` on failure).
- **Idempotent, status-gated.** A send only processes an `approved` `Draft` whose `Startup` is `queued` and which has no prior `initial` `Message`. Re-running never double-sends.
- **Pacing is pure.** `is_within_window`, `effective_daily_cap`, `sent_today`, `budget_remaining` take `now` as a parameter and never read the wall clock. Times are interpreted in `send_timezone` (`Asia/Kolkata`).
- **Secrets stay out of git.** SMTP credentials live in `.env` (already gitignored); `.env.example` carries them blank.
- **Service function is named `send_test_emails`, not `test_send`** — a top-level `test_send` imported into a test module would be collected and run by pytest. The CLI command is still spelled `test-send`.
- **No new dependency.** `dnspython>=2.6` is already in `pyproject.toml`.
- **Commit trailer** on every commit:
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  ```

---

### Task 1: Config settings, `CampaignState` model, and `state` accessors

**Files:**
- Modify: `app/config.py` (append sending settings to `Settings`)
- Modify: `app/models.py` (add `CampaignState`)
- Create: `app/send/__init__.py` (empty)
- Create: `app/send/state.py`
- Test: `tests/test_send_state.py`
- Modify: `.env.example` (append SMTP block)

**Interfaces:**
- Produces: `Settings` fields `smtp_host/smtp_port/smtp_user/smtp_password/from_email/from_name/test_recipient/send_start/send_end/send_timezone/daily_cap/ramp_daily_cap/ramp_days/dkim_selector`.
- Produces: `CampaignState` model (`id`, `paused`, `paused_reason`, `consecutive_failures`, `first_send_at`).
- Produces: `ensure_state(session)`, `pause(session, reason)`, `resume(session)`, `record_success(session)`, `record_failure(session) -> int`.

- [ ] **Step 1: Write the failing test** — create `tests/test_send_state.py`:

```python
from app.send.state import (
    ensure_state, pause, record_failure, record_success, resume,
)


def test_ensure_state_creates_singleton(session):
    a = ensure_state(session)
    b = ensure_state(session)
    assert a.id == 1 and b.id == 1
    assert not a.paused and a.consecutive_failures == 0
    assert a.first_send_at is None


def test_pause_and_resume(session):
    pause(session, "manual")
    s = ensure_state(session)
    assert s.paused and s.paused_reason == "manual"
    resume(session)
    s = ensure_state(session)
    assert not s.paused and s.paused_reason == "" and s.consecutive_failures == 0


def test_record_failure_increments_then_success_resets(session):
    assert record_failure(session) == 1
    assert record_failure(session) == 2
    state = record_success(session)
    assert state.consecutive_failures == 0
    assert state.first_send_at is not None


def test_resume_clears_failures(session):
    record_failure(session)
    record_failure(session)
    resume(session)
    assert ensure_state(session).consecutive_failures == 0
```

- [ ] **Step 2: Run it, expect failure** — `Run: .venv/Scripts/python -m pytest tests/test_send_state.py -v` → FAIL (`ModuleNotFoundError: app.send`).

- [ ] **Step 3: Add config settings** — in `app/config.py`, insert after the `hunter_monthly_limit: int = 25` line, before the closing of `class Settings`:

```python
    # SMTP / sending (Phase 4)
    smtp_host: str = "smtp.hostinger.com"
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    from_email: str = ""
    from_name: str = ""
    test_recipient: str = ""
    send_start: str = "09:30"
    send_end: str = "18:30"
    send_timezone: str = "Asia/Kolkata"
    daily_cap: int = 30
    ramp_daily_cap: int = 15
    ramp_days: int = 7
    dkim_selector: str = ""
```

- [ ] **Step 4: Add the `CampaignState` model** — append to `app/models.py` (all imports used — `Integer`, `String`, `DateTime`, `Mapped`, `mapped_column`, `datetime` — are already imported):

```python
class CampaignState(Base):
    __tablename__ = "campaign_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # singleton, id=1
    paused: Mapped[bool] = mapped_column(default=False)
    paused_reason: Mapped[str] = mapped_column(String(200), default="")
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    first_send_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
```

- [ ] **Step 5: Create `app/send/__init__.py`** — empty file.

- [ ] **Step 6: Create `app/send/state.py`:**

```python
from sqlalchemy.orm import Session

from app.models import CampaignState, utcnow


def ensure_state(session: Session) -> CampaignState:
    """Return the singleton campaign_state row (id=1), creating it if absent."""
    state = session.get(CampaignState, 1)
    if state is None:
        state = CampaignState(id=1)
        session.add(state)
        session.commit()
    return state


def pause(session: Session, reason: str) -> CampaignState:
    state = ensure_state(session)
    state.paused = True
    state.paused_reason = reason
    session.commit()
    return state


def resume(session: Session) -> CampaignState:
    state = ensure_state(session)
    state.paused = False
    state.paused_reason = ""
    state.consecutive_failures = 0
    session.commit()
    return state


def record_success(session: Session) -> CampaignState:
    """Reset the consecutive-failure counter; stamp first_send_at once."""
    state = ensure_state(session)
    state.consecutive_failures = 0
    if state.first_send_at is None:
        state.first_send_at = utcnow()
    session.commit()
    return state


def record_failure(session: Session) -> int:
    """Increment and return the consecutive-failure count."""
    state = ensure_state(session)
    state.consecutive_failures += 1
    session.commit()
    return state.consecutive_failures
```

- [ ] **Step 7: Run the tests, expect pass** — `Run: .venv/Scripts/python -m pytest tests/test_send_state.py -v` → PASS (4 tests).

- [ ] **Step 8: Append the SMTP block to `.env.example`** (append at end of file):

```
# SMTP / sending (Phase 4) — Hostinger mailbox
M2S_SMTP_HOST=smtp.hostinger.com
M2S_SMTP_PORT=465
M2S_SMTP_USER=
M2S_SMTP_PASSWORD=
M2S_FROM_EMAIL=
M2S_FROM_NAME=
M2S_TEST_RECIPIENT=
M2S_SEND_START=09:30
M2S_SEND_END=18:30
M2S_SEND_TIMEZONE=Asia/Kolkata
M2S_DAILY_CAP=30
M2S_RAMP_DAILY_CAP=15
M2S_RAMP_DAYS=7
# From your Hostinger DNS panel; leave blank to skip the DKIM preflight check
M2S_DKIM_SELECTOR=
```

- [ ] **Step 9: Run the full suite** — `Run: .venv/Scripts/python -m pytest -q` → all pass.

- [ ] **Step 10: Commit**

```bash
git add app/config.py app/models.py app/send/__init__.py app/send/state.py tests/test_send_state.py .env.example
git commit -m "feat: sending config, CampaignState model, and state accessors"
```

---

### Task 2: `pacing` — pure window / cap / ramp functions

**Files:**
- Create: `app/send/pacing.py`
- Test: `tests/test_pacing.py`

**Interfaces:**
- Consumes: `Message`, `MessageStatus` (from `app.models`).
- Produces: `is_within_window(now, *, start_hhmm, end_hhmm, tz) -> bool`; `effective_daily_cap(now, first_send_at, *, daily_cap, ramp_cap, ramp_days, tz) -> int`; `sent_today(session, now, *, tz) -> int`; `budget_remaining(session, now, first_send_at, *, daily_cap, ramp_cap, ramp_days, tz) -> int`.

- [ ] **Step 1: Write the failing test** — create `tests/test_pacing.py`:

```python
from datetime import datetime, timezone

from app.models import Message, MessageStatus
from app.send.pacing import (
    budget_remaining, effective_daily_cap, is_within_window, sent_today,
)

IST = "Asia/Kolkata"


def _utc(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


def test_within_window_weekday_inside():
    # 2026-08-12 is a Wednesday. 06:00 UTC = 11:30 IST → inside 09:30–18:30.
    assert is_within_window(_utc(2026, 8, 12, 6, 0),
                            start_hhmm="09:30", end_hhmm="18:30", tz=IST)


def test_outside_window_before_start():
    # 03:00 UTC = 08:30 IST → before 09:30.
    assert not is_within_window(_utc(2026, 8, 12, 3, 0),
                                start_hhmm="09:30", end_hhmm="18:30", tz=IST)


def test_outside_window_weekend():
    # 2026-08-15 is a Saturday.
    assert not is_within_window(_utc(2026, 8, 15, 6, 0),
                                start_hhmm="09:30", end_hhmm="18:30", tz=IST)


def test_effective_cap_ramp_then_steady():
    first = _utc(2026, 8, 12, 6, 0)
    within = _utc(2026, 8, 15, 6, 0)   # 3 days later → still ramp
    after = _utc(2026, 8, 20, 6, 0)    # 8 days later → steady
    assert effective_daily_cap(within, first, daily_cap=30, ramp_cap=15,
                               ramp_days=7, tz=IST) == 15
    assert effective_daily_cap(after, first, daily_cap=30, ramp_cap=15,
                               ramp_days=7, tz=IST) == 30


def test_effective_cap_no_first_send():
    assert effective_daily_cap(_utc(2026, 8, 12, 6, 0), None, daily_cap=30,
                               ramp_cap=15, ramp_days=7, tz=IST) == 15


def test_sent_today_counts_only_today(session):
    now = _utc(2026, 8, 12, 6, 0)
    session.add(Message(draft_id=1, status=MessageStatus.SENT,
                        sent_at=_utc(2026, 8, 12, 5, 0)))   # today IST
    session.add(Message(draft_id=2, status=MessageStatus.SENT,
                        sent_at=_utc(2026, 8, 11, 5, 0)))   # yesterday
    session.add(Message(draft_id=3, status=MessageStatus.QUEUED,
                        sent_at=_utc(2026, 8, 12, 5, 30)))  # not SENT
    session.commit()
    assert sent_today(session, now, tz=IST) == 1


def test_budget_remaining_floors_at_zero(session):
    now = _utc(2026, 8, 12, 6, 0)
    for i in range(3):
        session.add(Message(draft_id=i + 1, status=MessageStatus.SENT,
                            sent_at=_utc(2026, 8, 12, 5, 0)))
    session.commit()
    # ramp cap 2, already 3 sent → 0, not negative
    assert budget_remaining(session, now, None, daily_cap=30, ramp_cap=2,
                            ramp_days=7, tz=IST) == 0
```

- [ ] **Step 2: Run it, expect failure** — `Run: .venv/Scripts/python -m pytest tests/test_pacing.py -v` → FAIL (module missing).

- [ ] **Step 3: Create `app/send/pacing.py`:**

```python
from datetime import datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Message, MessageStatus


def _parse_hhmm(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


def is_within_window(now: datetime, *, start_hhmm: str, end_hhmm: str, tz: str) -> bool:
    """True on Mon–Fri when the tz-local clock time is within [start, end]."""
    local = now.astimezone(ZoneInfo(tz))
    if local.weekday() >= 5:  # Sat=5, Sun=6
        return False
    return _parse_hhmm(start_hhmm) <= local.time() <= _parse_hhmm(end_hhmm)


def effective_daily_cap(now: datetime, first_send_at: datetime | None, *,
                        daily_cap: int, ramp_cap: int, ramp_days: int, tz: str) -> int:
    """ramp_cap during the ramp window (or before any send), else daily_cap."""
    if first_send_at is None:
        return ramp_cap
    elapsed = (now.astimezone(ZoneInfo(tz)).date()
               - first_send_at.astimezone(ZoneInfo(tz)).date()).days
    return ramp_cap if elapsed < ramp_days else daily_cap


def sent_today(session: Session, now: datetime, *, tz: str) -> int:
    """Count SENT messages whose sent_at falls on today's tz-local date."""
    today = now.astimezone(ZoneInfo(tz)).date()
    rows = session.scalars(
        select(Message.sent_at).where(Message.status == MessageStatus.SENT)).all()
    return sum(1 for ts in rows
               if ts is not None and ts.astimezone(ZoneInfo(tz)).date() == today)


def budget_remaining(session: Session, now: datetime, first_send_at: datetime | None, *,
                     daily_cap: int, ramp_cap: int, ramp_days: int, tz: str) -> int:
    cap = effective_daily_cap(now, first_send_at, daily_cap=daily_cap,
                              ramp_cap=ramp_cap, ramp_days=ramp_days, tz=tz)
    return max(0, cap - sent_today(session, now, tz=tz))
```

- [ ] **Step 4: Run the tests, expect pass** — `Run: .venv/Scripts/python -m pytest tests/test_pacing.py -v` → PASS (7 tests).

- [ ] **Step 5: Run the full suite** — `Run: .venv/Scripts/python -m pytest -q` → all pass.

- [ ] **Step 6: Commit**

```bash
git add app/send/pacing.py tests/test_pacing.py
git commit -m "feat: pure send-pacing functions (window, cap, ramp)"
```

---

### Task 3: `smtp_client` — MIME builder + injectable transport

**Files:**
- Create: `app/send/smtp_client.py`
- Test: `tests/test_send_smtp.py`

**Interfaces:**
- Produces: `build_email(*, from_email, from_name, to, subject, body, pdf_path) -> EmailMessage`; `Transport` protocol with `.send(msg) -> str`; `SmtpTransport(host, port, user, password)`.

- [ ] **Step 1: Write the failing test** — create `tests/test_send_smtp.py`:

```python
from app.send.smtp_client import build_email


def test_build_email_sets_headers_and_body():
    msg = build_email(from_email="me@d.com", from_name="Me", to="you@x.io",
                      subject="Hi", body="Hello there", pdf_path=None)
    assert msg["To"] == "you@x.io"
    assert msg["Subject"] == "Hi"
    assert msg["From"] == "Me <me@d.com>"
    assert msg["Message-ID"]
    assert msg.get_content().strip() == "Hello there"


def test_build_email_no_name_uses_bare_address():
    msg = build_email(from_email="me@d.com", from_name="", to="you@x.io",
                      subject="Hi", body="b", pdf_path=None)
    assert msg["From"] == "me@d.com"


def test_build_email_casual_has_no_attachment():
    msg = build_email(from_email="me@d.com", from_name="Me", to="you@x.io",
                      subject="Hi", body="b", pdf_path=None)
    assert not list(msg.iter_attachments())


def test_build_email_formal_attaches_pdf(tmp_path):
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    msg = build_email(from_email="me@d.com", from_name="Me", to="you@x.io",
                      subject="Hi", body="b", pdf_path=str(pdf))
    attachments = list(msg.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "resume.pdf"
    assert attachments[0].get_content_type() == "application/pdf"


class _RecordingTransport:
    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)
        return msg["Message-ID"]


def test_recording_transport_returns_message_id():
    msg = build_email(from_email="me@d.com", from_name="Me", to="you@x.io",
                      subject="Hi", body="b", pdf_path=None)
    t = _RecordingTransport()
    returned = t.send(msg)
    assert returned == msg["Message-ID"]
    assert t.sent == [msg]
```

- [ ] **Step 2: Run it, expect failure** — `Run: .venv/Scripts/python -m pytest tests/test_send_smtp.py -v` → FAIL (module missing).

- [ ] **Step 3: Create `app/send/smtp_client.py`:**

```python
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path
from typing import Protocol


def build_email(*, from_email: str, from_name: str, to: str, subject: str,
                body: str, pdf_path: str | None) -> EmailMessage:
    """Build a plain-text email; attach the PDF when pdf_path is set.
    Sets a generated Message-ID header for later reply matching."""
    msg = EmailMessage()
    msg["From"] = formataddr((from_name, from_email)) if from_name else from_email
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid()
    msg.set_content(body)
    if pdf_path:
        data = Path(pdf_path).read_bytes()
        msg.add_attachment(data, maintype="application", subtype="pdf",
                           filename=Path(pdf_path).name)
    return msg


class Transport(Protocol):
    def send(self, msg: EmailMessage) -> str:
        """Send msg; return its Message-ID."""
        ...


class SmtpTransport:
    """Real SMTP_SSL transport. Not exercised in tests (they inject a fake)."""

    def __init__(self, host: str, port: int, user: str, password: str):
        self._host = host
        self._port = port
        self._user = user
        self._password = password

    def send(self, msg: EmailMessage) -> str:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(self._host, self._port, context=context) as smtp:
            smtp.login(self._user, self._password)
            smtp.send_message(msg)
        return msg["Message-ID"]
```

- [ ] **Step 4: Run the tests, expect pass** — `Run: .venv/Scripts/python -m pytest tests/test_send_smtp.py -v` → PASS (5 tests).

- [ ] **Step 5: Run the full suite** — `Run: .venv/Scripts/python -m pytest -q` → all pass.

- [ ] **Step 6: Commit**

```bash
git add app/send/smtp_client.py tests/test_send_smtp.py
git commit -m "feat: MIME email builder and injectable SMTP transport"
```

---

### Task 4: `service` — approve / reject

**Files:**
- Create: `app/send/service.py`
- Test: `tests/test_send_service.py`

**Interfaces:**
- Produces: `SendResult` dataclass (`draft_id: int`, `sent: bool`, `reason: str | None = None`); `approve_drafts(session, ids=None, *, all_pending=False) -> int`; `reject_drafts(session, ids) -> int`. (`send_batch` / `send_test_emails` are added in Task 5.)

- [ ] **Step 1: Write the failing test** — create `tests/test_send_service.py`:

```python
from sqlalchemy import select

from app.models import (
    Contact, Draft, DraftMode, DraftStatus, Startup, StartupStatus,
)
from app.send.service import approve_drafts, reject_drafts


def _drafted(session, name, domain, *, mode=DraftMode.FORMAL):
    s = Startup(name=name, domain=domain, source="yc", status=StartupStatus.DRAFTED)
    session.add(s)
    session.commit()
    c = Contact(startup_id=s.id, name="C", role="CTO", email=f"c@{domain}",
                found_via="scraped", confidence=0.9, verified=True)
    session.add(c)
    session.commit()
    d = Draft(startup_id=s.id, contact_id=c.id, mode=mode, subject="Hi", body="Hello",
              resume_pdf_path=None,  # None so build_email reads no file in send tests
              status=DraftStatus.PENDING_REVIEW)
    session.add(d)
    session.commit()
    return s, d


def test_approve_moves_draft_and_startup(session):
    s, d = _drafted(session, "A", "a.io")
    assert approve_drafts(session, [d.id]) == 1
    assert d.status == DraftStatus.APPROVED
    assert s.status == StartupStatus.QUEUED


def test_approve_all_pending(session):
    _drafted(session, "A", "a.io")
    _drafted(session, "B", "b.io")
    assert approve_drafts(session, all_pending=True) == 2
    assert len(session.scalars(
        select(Draft).where(Draft.status == DraftStatus.APPROVED)).all()) == 2


def test_reject_sets_startup_dead(session):
    s, d = _drafted(session, "A", "a.io")
    assert reject_drafts(session, [d.id]) == 1
    assert d.status == DraftStatus.REJECTED
    assert s.status == StartupStatus.DEAD
```

- [ ] **Step 2: Run it, expect failure** — `Run: .venv/Scripts/python -m pytest tests/test_send_service.py -v` → FAIL (module missing).

- [ ] **Step 3: Create `app/send/service.py`:**

```python
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Draft, DraftStatus, Event, Startup, StartupStatus,
)


@dataclass
class SendResult:
    draft_id: int
    sent: bool
    reason: str | None = None


def approve_drafts(session: Session, ids: list[int] | None = None, *,
                   all_pending: bool = False) -> int:
    """Approve drafts by id (or every pending_review draft). Moves each
    Draft→approved and its Startup→queued. Returns the count approved."""
    query = select(Draft).where(Draft.status == DraftStatus.PENDING_REVIEW)
    if not all_pending:
        query = query.where(Draft.id.in_(ids or []))
    drafts = session.scalars(query).all()
    for draft in drafts:
        draft.status = DraftStatus.APPROVED
        startup = session.get(Startup, draft.startup_id)
        if startup is not None:
            startup.status = StartupStatus.QUEUED
        session.add(Event(startup_id=draft.startup_id, kind="approved",
                          payload={"draft_id": draft.id}))
    session.commit()
    return len(drafts)


def reject_drafts(session: Session, ids: list[int]) -> int:
    """Reject drafts by id. Moves each Draft→rejected and its Startup→dead."""
    drafts = session.scalars(
        select(Draft).where(Draft.id.in_(ids),
                            Draft.status == DraftStatus.PENDING_REVIEW)).all()
    for draft in drafts:
        draft.status = DraftStatus.REJECTED
        startup = session.get(Startup, draft.startup_id)
        if startup is not None:
            startup.status = StartupStatus.DEAD
        session.add(Event(startup_id=draft.startup_id, kind="rejected",
                          payload={"draft_id": draft.id}))
    session.commit()
    return len(drafts)
```

- [ ] **Step 4: Run the tests, expect pass** — `Run: .venv/Scripts/python -m pytest tests/test_send_service.py -v` → PASS (3 tests).

- [ ] **Step 5: Run the full suite** — `Run: .venv/Scripts/python -m pytest -q` → all pass.

- [ ] **Step 6: Commit**

```bash
git add app/send/service.py tests/test_send_service.py
git commit -m "feat: approve/reject drafts service"
```

---

### Task 5: `service` — one-shot `send_batch` + `send_test_emails`

**Files:**
- Modify: `app/send/service.py` (replace import block; append send functions)
- Test: `tests/test_send_service.py` (append)

**Interfaces:**
- Consumes: `build_email` (Task 3), `budget_remaining`/`is_within_window` (Task 2), `state` accessors (Task 1).
- Produces: `send_batch(session, *, now, transport, settings, limit=1, dry_run=False, force=False) -> list[SendResult]`; `send_test_emails(session, *, transport, settings, count=5) -> int`.
- Gating (real sends): `paused` blocks (a dry-run is always allowed); `window` and `budget` are enforced unless `dry_run` or `force`. 3 consecutive SMTP failures → auto-pause.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_send_service.py`:

```python
import types
from datetime import datetime, timezone

from app.models import Event, Message, MessageStatus
from app.send import state as state_mod
from app.send.service import SendResult, send_batch, send_test_emails

NOW = datetime(2026, 8, 12, 6, 0, tzinfo=timezone.utc)  # Wed 11:30 IST


def _settings(**over):
    base = dict(from_email="me@d.com", from_name="Me", smtp_user="me@d.com",
                test_recipient="me@d.com", send_start="00:00", send_end="23:59",
                send_timezone="Asia/Kolkata", daily_cap=30, ramp_daily_cap=15,
                ramp_days=7)
    base.update(over)
    return types.SimpleNamespace(**base)


class _Recorder:
    def __init__(self, fail=False):
        self.fail = fail
        self.sent = []

    def send(self, msg):
        if self.fail:
            import smtplib
            raise smtplib.SMTPException("boom")
        self.sent.append(msg)
        return msg["Message-ID"]


def _queued(session, name, domain, *, mode=DraftMode.FORMAL):
    s, d = _drafted(session, name, domain, mode=mode)
    approve_drafts(session, [d.id])
    return s, d


def test_send_batch_sends_eligible(session):
    s, d = _queued(session, "A", "a.io")
    t = _Recorder()
    results = send_batch(session, now=NOW, transport=t, settings=_settings())
    assert len(t.sent) == 1 and results[0].sent is True
    assert s.status == StartupStatus.SENT
    msg = session.scalars(select(Message)).one()
    assert msg.status == MessageStatus.SENT and msg.smtp_message_id


def test_send_batch_idempotent(session):
    _queued(session, "A", "a.io")
    t = _Recorder()
    send_batch(session, now=NOW, transport=t, settings=_settings())
    second = send_batch(session, now=NOW, transport=t, settings=_settings())
    assert second == []          # nothing eligible the second time
    assert len(t.sent) == 1


def test_send_batch_respects_pause(session):
    _queued(session, "A", "a.io")
    state_mod.pause(session, "manual")
    t = _Recorder()
    results = send_batch(session, now=NOW, transport=t, settings=_settings())
    assert results == [SendResult(0, False, "paused")] and t.sent == []


def test_send_batch_outside_window(session):
    _queued(session, "A", "a.io")
    t = _Recorder()
    results = send_batch(session, now=NOW, transport=t,
                         settings=_settings(send_start="12:00", send_end="13:00"))
    assert results == [SendResult(0, False, "outside_window")] and t.sent == []


def test_send_batch_dry_run_to_test_recipient(session):
    s, d = _queued(session, "A", "a.io")
    t = _Recorder()
    results = send_batch(session, now=NOW, transport=t,
                         settings=_settings(test_recipient="self@me.com"),
                         dry_run=True)
    assert results[0].sent is True
    assert t.sent[0]["To"] == "self@me.com"
    assert s.status == StartupStatus.QUEUED               # unchanged
    assert session.scalars(select(Message)).all() == []   # nothing persisted


def test_send_batch_cap_reached(session):
    _queued(session, "A", "a.io")
    _queued(session, "B", "b.io")
    t = _Recorder()
    results = send_batch(session, now=NOW, transport=t,
                         settings=_settings(ramp_daily_cap=1), limit=5)
    assert len([r for r in results if r.sent]) == 1 and len(t.sent) == 1


def test_send_failure_logs_and_autopauses(session):
    for name, dom in [("A", "a.io"), ("B", "b.io"), ("C", "c.io")]:
        _queued(session, name, dom)
    t = _Recorder(fail=True)
    send_batch(session, now=NOW, transport=t, settings=_settings(), limit=3)
    state = state_mod.ensure_state(session)
    assert state.paused and "3 consecutive" in state.paused_reason
    failed = session.scalars(select(Event).where(Event.kind == "send_failed")).all()
    assert len(failed) == 3


def test_force_bypasses_window(session):
    _queued(session, "A", "a.io")
    t = _Recorder()
    results = send_batch(session, now=NOW, transport=t,
                         settings=_settings(send_start="12:00", send_end="13:00"),
                         force=True)
    assert results[0].sent is True and len(t.sent) == 1


def test_send_test_emails_to_self(session):
    t = _Recorder()
    n = send_test_emails(session, transport=t,
                         settings=_settings(test_recipient="self@me.com"), count=3)
    assert n == 3 and len(t.sent) == 3
    assert all(m["To"] == "self@me.com" for m in t.sent)
    ev = session.scalars(select(Event).where(Event.kind == "test_send")).one()
    assert ev.payload["sent"] == 3
```

- [ ] **Step 2: Run it, expect failure** — `Run: .venv/Scripts/python -m pytest tests/test_send_service.py -v` → FAIL (`ImportError: send_batch`).

- [ ] **Step 3: Replace the import block** at the top of `app/send/service.py` (everything above `@dataclass`) with:

```python
import smtplib
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Contact, Draft, DraftMode, DraftStatus, Event, Message, MessageStatus,
    MessageType, Startup, StartupStatus,
)
from app.send import state as state_mod
from app.send.pacing import budget_remaining, is_within_window
from app.send.smtp_client import build_email

_FAILURE_PAUSE_THRESHOLD = 3
```

- [ ] **Step 4: Append the send functions** to `app/send/service.py`:

```python
def _eligible_drafts(session: Session, limit: int) -> list[Draft]:
    return session.scalars(
        select(Draft)
        .join(Startup, Draft.startup_id == Startup.id)
        .where(Draft.status == DraftStatus.APPROVED,
               Startup.status == StartupStatus.QUEUED,
               Draft.id.not_in(
                   select(Message.draft_id).where(Message.type == MessageType.INITIAL)))
        .order_by(Draft.id)
        .limit(limit)
    ).all()


def send_batch(session: Session, *, now, transport, settings, limit: int = 1,
               dry_run: bool = False, force: bool = False) -> list[SendResult]:
    state = state_mod.ensure_state(session)
    if state.paused and not dry_run:
        return [SendResult(0, False, "paused")]
    if not dry_run and not force and not is_within_window(
            now, start_hhmm=settings.send_start, end_hhmm=settings.send_end,
            tz=settings.send_timezone):
        return [SendResult(0, False, "outside_window")]

    results: list[SendResult] = []
    for draft in _eligible_drafts(session, limit):
        if not dry_run and not force and budget_remaining(
                session, now, state.first_send_at, daily_cap=settings.daily_cap,
                ramp_cap=settings.ramp_daily_cap, ramp_days=settings.ramp_days,
                tz=settings.send_timezone) <= 0:
            results.append(SendResult(draft.id, False, "cap_reached"))
            break
        results.append(_send_one(session, draft, now=now, transport=transport,
                                 settings=settings, dry_run=dry_run))
    return results


def _send_one(session: Session, draft: Draft, *, now, transport, settings,
              dry_run: bool) -> SendResult:
    sid, did = draft.startup_id, draft.id
    contact = session.get(Contact, draft.contact_id) if draft.contact_id else None
    to_addr = settings.test_recipient if dry_run else (contact.email if contact else None)
    if not to_addr:
        session.add(Event(startup_id=sid, kind="send_failed",
                          payload={"draft_id": did, "reason": "no_recipient"}))
        session.commit()
        return SendResult(did, False, "no_recipient")

    msg = build_email(
        from_email=settings.from_email or settings.smtp_user,
        from_name=settings.from_name, to=to_addr, subject=draft.subject,
        body=draft.body,
        pdf_path=draft.resume_pdf_path if draft.mode == DraftMode.FORMAL else None)
    try:
        message_id = transport.send(msg)
    except (smtplib.SMTPException, OSError) as exc:
        session.rollback()
        session.add(Event(startup_id=sid, kind="send_failed",
                          payload={"draft_id": did, "reason": "smtp_error",
                                   "detail": str(exc)}))
        session.commit()
        if state_mod.record_failure(session) >= _FAILURE_PAUSE_THRESHOLD:
            state_mod.pause(session, "auto: 3 consecutive send failures")
        return SendResult(did, False, "smtp_error")

    if dry_run:
        session.add(Event(startup_id=sid, kind="dry_run_send",
                          payload={"draft_id": did, "to": to_addr}))
        session.commit()
        return SendResult(did, True, "dry_run")

    session.add(Message(draft_id=did, type=MessageType.INITIAL, sent_at=now,
                        smtp_message_id=message_id, status=MessageStatus.SENT))
    startup = session.get(Startup, sid)
    if startup is not None:
        startup.status = StartupStatus.SENT
    session.add(Event(startup_id=sid, kind="sent",
                      payload={"draft_id": did, "message_id": message_id}))
    session.commit()
    state_mod.record_success(session)
    return SendResult(did, True, None)


def send_test_emails(session: Session, *, transport, settings, count: int = 5) -> int:
    """Send `count` canned emails to settings.test_recipient to confirm auth and
    inbox placement. Does not touch drafts/startups."""
    to_addr = settings.test_recipient
    from_email = settings.from_email or settings.smtp_user
    sent = 0
    for i in range(count):
        msg = build_email(from_email=from_email, from_name=settings.from_name,
                          to=to_addr, subject=f"Mail2Startups test {i + 1}/{count}",
                          body="Deliverability test send. Safe to ignore.",
                          pdf_path=None)
        try:
            transport.send(msg)
        except (smtplib.SMTPException, OSError):
            break
        sent += 1
    session.add(Event(startup_id=None, kind="test_send",
                      payload={"requested": count, "sent": sent}))
    session.commit()
    return sent
```

- [ ] **Step 5: Run the tests, expect pass** — `Run: .venv/Scripts/python -m pytest tests/test_send_service.py -v` → PASS (all Task 4 + Task 5 tests).

- [ ] **Step 6: Run the full suite** — `Run: .venv/Scripts/python -m pytest -q` → all pass.

- [ ] **Step 7: Commit**

```bash
git add app/send/service.py tests/test_send_service.py
git commit -m "feat: one-shot send_batch with pacing, dry-run, and auto-pause"
```

---

### Task 6: `preflight` — SPF / DKIM / DMARC via injectable resolver

**Files:**
- Create: `app/send/preflight.py`
- Test: `tests/test_preflight.py`

**Interfaces:**
- Produces: `PreflightReport` dataclass (`spf`, `dkim`, `dmarc`: each `tuple[bool, str]`; `.ok` property); `check_dns(domain, *, selector, resolve) -> PreflightReport` where `resolve(name, rtype) -> list[str]`.

- [ ] **Step 1: Write the failing test** — create `tests/test_preflight.py`:

```python
from app.send.preflight import check_dns


def _resolver(records):
    def resolve(name, rtype):
        return records.get(name, [])
    return resolve


def test_all_pass():
    resolve = _resolver({
        "d.com": ["v=spf1 include:hostinger.com ~all"],
        "_dmarc.d.com": ["v=DMARC1; p=none"],
        "sel._domainkey.d.com": ["v=DKIM1; k=rsa; p=ABC"],
    })
    report = check_dns("d.com", selector="sel", resolve=resolve)
    assert report.ok
    assert report.spf[0] and report.dmarc[0] and report.dkim[0]


def test_missing_spf_and_dmarc_fail():
    resolve = _resolver({"sel._domainkey.d.com": ["v=DKIM1; p=ABC"]})
    report = check_dns("d.com", selector="sel", resolve=resolve)
    assert not report.ok
    assert not report.spf[0] and not report.dmarc[0]
    assert "v=spf1" in report.spf[1]


def test_blank_selector_skips_dkim():
    resolve = _resolver({
        "d.com": ["v=spf1 ~all"],
        "_dmarc.d.com": ["v=DMARC1"],
    })
    report = check_dns("d.com", selector="", resolve=resolve)
    assert report.ok                       # spf+dmarc pass, dkim skipped counts as pass
    assert "skipped" in report.dkim[1]
```

- [ ] **Step 2: Run it, expect failure** — `Run: .venv/Scripts/python -m pytest tests/test_preflight.py -v` → FAIL (module missing).

- [ ] **Step 3: Create `app/send/preflight.py`:**

```python
from dataclasses import dataclass


@dataclass
class PreflightReport:
    spf: tuple[bool, str]
    dkim: tuple[bool, str]
    dmarc: tuple[bool, str]

    @property
    def ok(self) -> bool:
        return self.spf[0] and self.dkim[0] and self.dmarc[0]


def _txt_contains(resolve, name: str, marker: str) -> tuple[bool, str]:
    for record in resolve(name, "TXT"):
        if marker.lower() in record.lower():
            return True, record
    return False, f"no TXT at {name} containing {marker!r}"


def check_dns(domain: str, *, selector: str, resolve) -> PreflightReport:
    """resolve(name, 'TXT') -> list[str]. SPF on the domain, DMARC on
    _dmarc.<domain>, DKIM on <selector>._domainkey.<domain> (skipped when
    selector is blank)."""
    spf = _txt_contains(resolve, domain, "v=spf1")
    dmarc = _txt_contains(resolve, f"_dmarc.{domain}", "v=DMARC1")
    if selector:
        dkim = _txt_contains(resolve, f"{selector}._domainkey.{domain}", "v=DKIM1")
    else:
        dkim = (True, "skipped: no M2S_DKIM_SELECTOR set")
    return PreflightReport(spf=spf, dkim=dkim, dmarc=dmarc)
```

- [ ] **Step 4: Run the tests, expect pass** — `Run: .venv/Scripts/python -m pytest tests/test_preflight.py -v` → PASS (3 tests).

- [ ] **Step 5: Run the full suite** — `Run: .venv/Scripts/python -m pytest -q` → all pass.

- [ ] **Step 6: Commit**

```bash
git add app/send/preflight.py tests/test_preflight.py
git commit -m "feat: SPF/DKIM/DMARC DNS preflight check"
```

---

### Task 7: CLI commands + README

**Files:**
- Modify: `app/cli.py` (add imports, helpers, and the `approve`/`reject`/`send`/`pause`/`resume`/`test-send`/`preflight` commands)
- Test: `tests/test_cli_send.py`
- Modify: `README.md` (add a "Sending (Phase 4)" section)

**Interfaces:**
- Consumes: everything from Tasks 1–6 plus `get_settings`, `_session` (existing).
- Produces: CLI commands and two module-level helpers `_build_transport(settings)` and `_dns_resolve(name, rtype)` (both monkeypatched in tests).

- [ ] **Step 1: Write the failing test** — create `tests/test_cli_send.py`:

```python
from sqlalchemy import select
from typer.testing import CliRunner

import app.cli as cli_mod
from app.cli import app
from app.db import get_engine, init_db, make_session
from app.models import (
    Contact, Draft, DraftMode, DraftStatus, Message, MessageStatus,
    Startup, StartupStatus,
)

runner = CliRunner()


def _prepare(monkeypatch, tmp_path):
    db = tmp_path / "test.db"
    monkeypatch.setenv("M2S_DB_PATH", str(db))
    monkeypatch.setenv("M2S_SMTP_USER", "me@d.com")
    monkeypatch.setenv("M2S_SMTP_PASSWORD", "secret")
    monkeypatch.setenv("M2S_FROM_EMAIL", "me@d.com")
    monkeypatch.setenv("M2S_TEST_RECIPIENT", "self@me.com")
    runner.invoke(app, ["init-db"])
    return db


def _seed_drafted(db, name, domain):
    engine = get_engine(db)
    init_db(engine)
    with make_session(engine) as s:
        startup = Startup(name=name, domain=domain, source="yc",
                          status=StartupStatus.DRAFTED)
        s.add(startup); s.commit()
        c = Contact(startup_id=startup.id, name="C", role="CTO", email=f"c@{domain}",
                    found_via="scraped", confidence=0.9, verified=True)
        s.add(c); s.commit()
        d = Draft(startup_id=startup.id, contact_id=c.id, mode=DraftMode.FORMAL,
                  subject="Hi", body="Hello", resume_pdf_path=None,
                  status=DraftStatus.PENDING_REVIEW)
        s.add(d); s.commit()
        return s.scalars(select(Draft.id)).first()


class _Recorder:
    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)
        return msg["Message-ID"]


def test_approve_then_send(monkeypatch, tmp_path):
    db = _prepare(monkeypatch, tmp_path)
    draft_id = _seed_drafted(db, "A", "a.io")

    approved = runner.invoke(app, ["approve", str(draft_id)])
    assert approved.exit_code == 0 and "approved 1" in approved.output

    rec = _Recorder()
    monkeypatch.setattr(cli_mod, "_build_transport", lambda settings: rec)
    sent = runner.invoke(app, ["send", "--force"])
    assert sent.exit_code == 0 and "sent=1" in sent.output
    assert len(rec.sent) == 1
    with make_session(get_engine(db)) as s:
        assert s.scalars(select(Message)).one().status == MessageStatus.SENT


def test_reject_marks_dead(monkeypatch, tmp_path):
    db = _prepare(monkeypatch, tmp_path)
    draft_id = _seed_drafted(db, "A", "a.io")
    out = runner.invoke(app, ["reject", str(draft_id)])
    assert out.exit_code == 0 and "rejected 1" in out.output
    with make_session(get_engine(db)) as s:
        assert s.scalars(select(Startup)).one().status == StartupStatus.DEAD


def test_send_requires_credentials(monkeypatch, tmp_path):
    db = tmp_path / "t.db"
    monkeypatch.setenv("M2S_DB_PATH", str(db))
    monkeypatch.setenv("M2S_SMTP_USER", "")       # explicit "" beats any .env file value
    monkeypatch.setenv("M2S_SMTP_PASSWORD", "")
    runner.invoke(app, ["init-db"])
    out = runner.invoke(app, ["send"])
    assert out.exit_code == 1 and "M2S_SMTP_USER" in out.output


def test_pause_and_resume(monkeypatch, tmp_path):
    from app.send.state import ensure_state
    db = _prepare(monkeypatch, tmp_path)
    assert runner.invoke(app, ["pause"]).exit_code == 0
    with make_session(get_engine(db)) as s:
        assert ensure_state(s).paused
    runner.invoke(app, ["resume"])
    with make_session(get_engine(db)) as s:
        assert not ensure_state(s).paused


def test_test_send(monkeypatch, tmp_path):
    _prepare(monkeypatch, tmp_path)
    rec = _Recorder()
    monkeypatch.setattr(cli_mod, "_build_transport", lambda settings: rec)
    out = runner.invoke(app, ["test-send", "--count", "3"])
    assert out.exit_code == 0 and "sent 3/3" in out.output
    assert len(rec.sent) == 3


def test_preflight(monkeypatch, tmp_path):
    _prepare(monkeypatch, tmp_path)
    monkeypatch.setenv("M2S_DKIM_SELECTOR", "sel")

    def fake_resolve(name, rtype):
        return {
            "d.com": ["v=spf1 ~all"],
            "_dmarc.d.com": ["v=DMARC1"],
            "sel._domainkey.d.com": ["v=DKIM1; p=x"],
        }.get(name, [])

    monkeypatch.setattr(cli_mod, "_dns_resolve", fake_resolve)
    out = runner.invoke(app, ["preflight"])
    assert out.exit_code == 0 and "all checks passed" in out.output
```

- [ ] **Step 2: Run it, expect failure** — `Run: .venv/Scripts/python -m pytest tests/test_cli_send.py -v` → FAIL (commands not defined).

- [ ] **Step 3: Add imports to `app/cli.py`** — add these alongside the existing imports at the top:

```python
from datetime import datetime, timezone

from app.send.preflight import check_dns
from app.send.service import (
    approve_drafts, reject_drafts, send_batch, send_test_emails,
)
from app.send.smtp_client import SmtpTransport
from app.send import state as send_state
```

- [ ] **Step 4: Add the helpers and commands** to `app/cli.py` — insert before `if __name__ == "__main__":`:

```python
def _build_transport(settings):
    return SmtpTransport(settings.smtp_host, settings.smtp_port,
                         settings.smtp_user, settings.smtp_password)


def _dns_resolve(name: str, rtype: str) -> list[str]:
    import dns.exception
    import dns.resolver
    try:
        answers = dns.resolver.resolve(name, rtype)
    except dns.exception.DNSException:
        return []
    return [b"".join(r.strings).decode("utf-8", "ignore") for r in answers]


@app.command()
def approve(
    ids: list[int] = typer.Argument(None, help="Draft ids to approve"),
    all_pending: bool = typer.Option(False, "--all", help="Approve all pending_review drafts"),
):
    """Approve drafts for sending (moves them to the send queue)."""
    if not ids and not all_pending:
        typer.echo("Error: give draft ids or --all", err=True)
        raise typer.Exit(code=1)
    with _session() as session:
        n = approve_drafts(session, ids, all_pending=all_pending)
    typer.echo(f"approved {n} draft(s)")


@app.command()
def reject(ids: list[int] = typer.Argument(..., help="Draft ids to reject")):
    """Reject drafts (marks their startups dead)."""
    with _session() as session:
        n = reject_drafts(session, ids)
    typer.echo(f"rejected {n} draft(s)")


@app.command()
def send(
    limit: int = typer.Option(1, help="Max emails to send this run"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Send to your own address; change nothing"),
    force: bool = typer.Option(False, "--force", help="Bypass the send window and daily cap"),
):
    """Send approved drafts (one-shot; respects pause, window, cap, ramp)."""
    settings = get_settings()
    if not settings.smtp_user or not settings.smtp_password:
        typer.echo("Error: set M2S_SMTP_USER and M2S_SMTP_PASSWORD in .env", err=True)
        raise typer.Exit(code=1)
    transport = _build_transport(settings)
    now = datetime.now(timezone.utc)
    with _session() as session:
        results = send_batch(session, now=now, transport=transport, settings=settings,
                             limit=limit, dry_run=dry_run, force=force)
    sent = sum(1 for r in results if r.sent)
    for r in results:
        typer.echo(f"  draft {r.draft_id}: {'sent' if r.sent else 'skip'} ({r.reason or 'ok'})")
    typer.echo(f"send: attempted={len(results)} sent={sent}")


@app.command()
def pause():
    """Pause sending."""
    with _session() as session:
        send_state.pause(session, "manual")
    typer.echo("sending paused")


@app.command()
def resume():
    """Resume sending and clear the failure counter."""
    with _session() as session:
        send_state.resume(session)
    typer.echo("sending resumed")


@app.command("test-send")
def test_send_cmd(count: int = typer.Option(5, help="How many test emails to send")):
    """Send test emails to M2S_TEST_RECIPIENT to check deliverability."""
    settings = get_settings()
    if not settings.test_recipient:
        typer.echo("Error: set M2S_TEST_RECIPIENT in .env", err=True)
        raise typer.Exit(code=1)
    transport = _build_transport(settings)
    with _session() as session:
        n = send_test_emails(session, transport=transport, settings=settings, count=count)
    typer.echo(f"test-send: sent {n}/{count} to {settings.test_recipient}")


@app.command()
def preflight(domain: str = typer.Option(None, help="Domain to check (default: from M2S_FROM_EMAIL)")):
    """Check SPF / DKIM / DMARC DNS records before the first send."""
    settings = get_settings()
    dom = domain or (settings.from_email or settings.smtp_user).split("@")[-1]
    if not dom:
        typer.echo("Error: no domain (set M2S_FROM_EMAIL or pass --domain)", err=True)
        raise typer.Exit(code=1)
    report = check_dns(dom, selector=settings.dkim_selector, resolve=_dns_resolve)
    for label, (ok, detail) in [("SPF", report.spf), ("DKIM", report.dkim),
                                ("DMARC", report.dmarc)]:
        typer.echo(f"  {label}: {'OK' if ok else 'FAIL'} — {detail}")
    typer.echo(f"preflight: {'all checks passed' if report.ok else 'issues found'}")
```

- [ ] **Step 5: Run the tests, expect pass** — `Run: .venv/Scripts/python -m pytest tests/test_cli_send.py -v` → PASS (6 tests).

- [ ] **Step 6: Add the README section** — insert after the "## Email hunting (Phase 2)" section (before "## Tests"):

```markdown
## Sending (Phase 4)

Approved drafts are sent from your Hostinger mailbox, one-shot and drip-paced.

```bash
m2s preflight                 # check SPF/DKIM/DMARC before the first send
m2s test-send --count 5       # 5 test emails to M2S_TEST_RECIPIENT (inbox-placement check)
m2s approve 12 34             # or: m2s approve --all
m2s send --dry-run            # sends to your own address, changes nothing
m2s send                      # sends the next-due email (respects pause, window, cap, ramp)
m2s pause / m2s resume        # halt / resume sending
```

`m2s send` sends up to `--limit` (default 1) approved emails per run, then exits;
schedule it every ~20 min during the send window (Windows Task Scheduler / cron)
so the randomized gap lives in the schedule. The daily cap (30, ramped to 15 for
the first 7 days) and "machine-off catch-up" both follow from re-running the
command. Three consecutive SMTP failures auto-pause the campaign.

Set the SMTP block in `.env` (see `.env.example`); `M2S_DKIM_SELECTOR` comes from
your Hostinger DNS panel.
```

- [ ] **Step 7: Run the full suite** — `Run: .venv/Scripts/python -m pytest -q` → all pass.

- [ ] **Step 8: Commit**

```bash
git add app/cli.py tests/test_cli_send.py README.md
git commit -m "feat: m2s approve/reject/send/pause/resume/test-send/preflight CLI"
```

---

## Self-Review Notes

- **Spec coverage:** approval (T4), one-shot send + pacing + dry-run + auto-pause (T2/T5), pause/resume + state (T1/T5/T7), preflight (T6/T7), test sends (T5/T7), config + docs (T1/T7). Follow-ups, daemon, dashboard, inbox — deferred per spec.
- **Type consistency:** `SendResult(draft_id, sent, reason=None)` used identically across T4/T5/T7. `resolve(name, rtype) -> list[str]` matches the fake resolvers in T6/T7 and `_dns_resolve` in T7. `settings` is duck-typed (`SimpleNamespace` in tests, real `Settings` in the CLI) — every field read (`from_email`, `from_name`, `smtp_user`, `test_recipient`, `send_start/end`, `send_timezone`, `daily_cap`, `ramp_daily_cap`, `ramp_days`) exists on both.
- **Naming:** service function is `send_test_emails` (not `test_send`) to avoid pytest collecting it; CLI command is `test-send` via `test_send_cmd`.
- **Offline:** every test injects a recording transport and/or a fake resolver; `SmtpTransport` and `dns.resolver` are never touched in tests.
