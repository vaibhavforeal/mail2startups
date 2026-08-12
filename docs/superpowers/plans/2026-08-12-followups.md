# Follow-ups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send one Claude-drafted, threaded follow-up to a startup that received an initial email and stayed silent past a delay window, reusing the existing review-and-send pipeline.

**Architecture:** A follow-up is an ordinary `Draft` row tagged `type=FOLLOWUP`, generated into `pending_review`, approved/rejected with the existing commands, and shipped by the existing `send` command. The send path is lightly generalized to select any not-yet-sent approved draft and to set `In-Reply-To`/`References` + `Message.type=FOLLOWUP` when the draft is a follow-up. The Phase-5 `no_response` sweep already measures from the newest send; a guard keeps a startup from being given up while its follow-up awaits review.

**Tech Stack:** Python 3.12+, SQLAlchemy 2.x + SQLite, pydantic-settings, Typer, anthropic SDK (injected/faked in tests). Tests run with `.venv/Scripts/python -m pytest`.

## Global Constraints

- **Offline tests only.** Every test injects a fake generator/transport; no network, no real Claude/SMTP. Follow the existing fake pattern: `_Client([outputs]).messages.parse(**kwargs) -> obj.parsed_output`.
- **Reuse the `MessageType` enum** (`app/models.py`: `INITIAL="initial"`, `FOLLOWUP="followup"`). Do not add a new enum.
- **Enum columns** are declared `Enum(SomeEnum, values_callable=lambda e: [m.value for m in e])` — match this exactly for any enum column.
- **One follow-up ever, per startup.** Generation must be idempotent across runs.
- **Give-up clock = 14 days after the most recent outbound message** (`no_response_days`, default 14). No change to that measurement — it already reads the newest send.
- **Follow-up delay default = 5 days** (`followup_delay_days`).
- **Threading:** a follow-up sets `In-Reply-To` and `References` to the initial send's stored `Message.smtp_message_id`; subject is `Re: <original subject>`; `mode=CASUAL` so no resume PDF is attached.
- **SQLite tz-naive gotcha:** `DateTime(timezone=True)` round-trips as tz-NAIVE. Pin a value read back from the DB to UTC (`dt.replace(tzinfo=timezone.utc)`) before comparing to an aware `now`. Each module carries its own tiny `_as_utc` helper (as `pacing.py` and `inbox/service.py` already do).
- **Review gate is reused unchanged.** No auto-send. `approve`/`reject`/`send` are not duplicated.
- **Resume fixture** for tests: `tests/fixtures/resume_min.yaml`, loaded via `from app.draft.resume_schema import load_resume`.
- **`session` fixture** (from `tests/conftest.py`) is an in-memory SQLite session with FK enforcement OFF — tests may reference `startup_id`/`draft_id` that need not exist as rows.

---

## File Structure

- **Modify** `app/models.py` — add `Draft.type` column (reuse `MessageType`).
- **Modify** `app/config.py` — add `followup_delay_days: int = 5`.
- **Create** `app/followup/__init__.py` — empty package marker.
- **Create** `app/followup/claude_followup.py` — `FollowupPlan`, `followup_plan(...)`, `MalformedFollowupError`.
- **Create** `app/followup/service.py` — `FollowupResult`, `draft_followups(...)` + private helpers.
- **Modify** `app/send/smtp_client.py` — `build_email` gains `in_reply_to`/`references`.
- **Modify** `app/send/service.py` — generalize `_eligible_drafts`; thread + type in `_send_one`.
- **Modify** `app/inbox/service.py` — `_sweep_no_response` skips startups with an in-flight follow-up.
- **Modify** `app/cli.py` — `followups` command + `_build_followup_generator`.
- **Modify** `.env.example`, `README.md` — config + docs.

---

## Task 1: Data model + config

**Files:**
- Modify: `app/models.py` (class `Draft`, after the `mode` column ~line 108-111)
- Modify: `app/config.py` (after `no_response_days`, line 45)
- Test: `tests/test_followup_models.py`

**Interfaces:**
- Produces: `Draft.type: Mapped[MessageType]` (default `MessageType.INITIAL`); `Settings.followup_delay_days: int = 5`.
- Consumes: existing `MessageType` enum (already defined in `app/models.py`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_followup_models.py`:

```python
from app.config import Settings
from app.models import Draft, DraftStatus, MessageType


def test_draft_defaults_to_initial_type(session):
    d = Draft(startup_id=1, subject="Hi", body="Hello",
              status=DraftStatus.PENDING_REVIEW)
    session.add(d)
    session.commit()
    assert d.type == MessageType.INITIAL


def test_draft_can_be_followup_type(session):
    d = Draft(startup_id=1, type=MessageType.FOLLOWUP, subject="Re: Hi",
              body="Bump", status=DraftStatus.PENDING_REVIEW)
    session.add(d)
    session.commit()
    assert d.type == MessageType.FOLLOWUP


def test_settings_followup_delay_default(monkeypatch):
    monkeypatch.delenv("M2S_FOLLOWUP_DELAY_DAYS", raising=False)
    assert Settings(_env_file=None).followup_delay_days == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_followup_models.py -v`
Expected: FAIL — `TypeError`/`AttributeError` on unknown `type` column, or `AttributeError` for `followup_delay_days`.

- [ ] **Step 3: Add the `Draft.type` column**

In `app/models.py`, inside `class Draft`, immediately after the `mode` column block (the `mode: Mapped[DraftMode] = mapped_column(...)` ending at `)` around line 111), add:

```python
    type: Mapped[MessageType] = mapped_column(
        Enum(MessageType, values_callable=lambda e: [m.value for m in e]),
        default=MessageType.INITIAL,
    )
```

(`MessageType`, `Enum`, and `mapped_column` are already imported/defined in this file.)

- [ ] **Step 4: Add the config field**

In `app/config.py`, immediately after `no_response_days: int = 14` (line 45), add:

```python
    followup_delay_days: int = 5
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_followup_models.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Run the full suite (no regressions)**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass (existing `Draft(...)` constructions default `type` to INITIAL).

- [ ] **Step 7: Commit**

```bash
git add app/models.py app/config.py tests/test_followup_models.py
git commit -m "feat: Draft.type column and followup_delay_days config"
```

---

## Task 2: Follow-up generation (Claude)

**Files:**
- Create: `app/followup/__init__.py`
- Create: `app/followup/claude_followup.py`
- Test: `tests/test_claude_followup.py`

**Interfaces:**
- Consumes: `resolve_backend()` from `app.draft.claude_draft`; `Resume` from `app.draft.resume_schema`; `get_settings()` from `app.config`.
- Produces:
  - `class FollowupPlan(BaseModel): body: str`
  - `class MalformedFollowupError(Exception)`
  - `def followup_plan(startup, resume, original_subject, original_body, *, client=None, model=None) -> FollowupPlan`
  - `def build_prompt(original_subject, original_body, resume) -> str`

- [ ] **Step 1: Write the failing test**

Create `tests/test_claude_followup.py`:

```python
from pathlib import Path

import pytest

from app.draft.resume_schema import load_resume
from app.followup.claude_followup import (
    FollowupPlan,
    MalformedFollowupError,
    build_prompt,
    followup_plan,
)

FIXTURE = Path(__file__).parent / "fixtures" / "resume_min.yaml"


class _Resp:
    def __init__(self, parsed):
        self.parsed_output = parsed


class _Messages:
    def __init__(self, outputs):
        self._outputs = list(outputs)
        self.calls = 0
        self.last_kwargs = None

    def parse(self, **kwargs):
        self.last_kwargs = kwargs
        out = self._outputs[self.calls]
        self.calls += 1
        return _Resp(out)


class _Client:
    def __init__(self, outputs):
        self.messages = _Messages(outputs)


class _Startup:
    name = "Globex"


def test_followup_plan_returns_parsed():
    client = _Client([FollowupPlan(body="Just bumping this up.")])
    plan = followup_plan(_Startup(), load_resume(FIXTURE), "Intern application",
                         "Original body", client=client)
    assert plan.body == "Just bumping this up."
    assert client.messages.calls == 1


def test_followup_plan_retries_then_raises():
    client = _Client([None, None])
    with pytest.raises(MalformedFollowupError):
        followup_plan(_Startup(), load_resume(FIXTURE), "S", "B", client=client)
    assert client.messages.calls == 2  # one retry


def test_followup_plan_retry_succeeds():
    client = _Client([None, FollowupPlan(body="bump")])
    plan = followup_plan(_Startup(), load_resume(FIXTURE), "S", "B", client=client)
    assert plan.body == "bump"
    assert client.messages.calls == 2


def test_build_prompt_includes_original_and_grounding():
    prompt = build_prompt("My subject", "My body text", load_resume(FIXTURE))
    assert "My subject" in prompt and "My body text" in prompt
    assert "NEVER invent" in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_claude_followup.py -v`
Expected: FAIL — `ModuleNotFoundError: app.followup.claude_followup`.

- [ ] **Step 3: Create the package marker**

Create `app/followup/__init__.py` (empty file).

- [ ] **Step 4: Implement the generator**

Create `app/followup/claude_followup.py`:

```python
import json

from pydantic import BaseModel

from app.config import get_settings
from app.draft.claude_draft import resolve_backend
from app.draft.resume_schema import Resume

MAX_TOKENS = 512


class FollowupPlan(BaseModel):
    body: str


class MalformedFollowupError(Exception):
    """The Claude response could not be parsed into a FollowupPlan after a retry."""


_PROMPT = (
    "You are writing a SHORT follow-up nudge to a startup that has not replied "
    "to the internship-outreach email below. Keep it to 2-3 sentences, plain "
    "and low-pressure. You may ONLY reference facts already in the original "
    "email or the resume — NEVER invent projects, numbers, or experience.\n\n"
    "ORIGINAL SUBJECT: {subject}\n\n"
    "ORIGINAL EMAIL BODY:\n{body}\n\n"
    "CANDIDATE RESUME (JSON, for grounding only):\n{resume_json}\n\n"
    "Write only the follow-up body (no subject line, no signature block)."
)


def build_prompt(original_subject, original_body, resume: Resume) -> str:
    return _PROMPT.format(
        subject=original_subject,
        body=original_body,
        resume_json=json.dumps(resume.model_dump(), ensure_ascii=False),
    )


def followup_plan(startup, resume: Resume, original_subject, original_body,
                  *, client=None, model=None) -> FollowupPlan:
    if client is None:
        client, resolved = resolve_backend()
        model = model or resolved
    else:
        model = model or get_settings().anthropic_model
    prompt = build_prompt(original_subject, original_body, resume)
    for attempt in range(2):
        content = prompt
        if attempt == 1:
            content += ("\n\nYour previous response could not be parsed. Respond "
                        "again, strictly matching the required schema.")
        response = client.messages.parse(
            model=model,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": content}],
            output_format=FollowupPlan,
        )
        if response.parsed_output is not None:
            return response.parsed_output
    raise MalformedFollowupError(
        f"follow-up response for {startup.name!r} could not be parsed after a retry"
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_claude_followup.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add app/followup/__init__.py app/followup/claude_followup.py tests/test_claude_followup.py
git commit -m "feat: Claude follow-up generation (grounded, threaded-body)"
```

---

## Task 3: Follow-up service (`draft_followups`)

**Files:**
- Create: `app/followup/service.py`
- Test: `tests/test_followup_service.py`

**Interfaces:**
- Consumes: `followup_plan`, `FollowupPlan`, `MalformedFollowupError` (Task 2); `Draft.type` (Task 1); models `Draft, DraftMode, DraftStatus, Event, Message, MessageStatus, MessageType, Startup, StartupStatus`.
- Produces:
  - `@dataclass class FollowupResult: startup_id: int; drafted: bool`
  - `def draft_followups(session, *, resume, now, settings, generator=followup_plan, limit=50, dry_run=False) -> list[FollowupResult]`

**Eligibility (all must hold):** startup `status==SENT`; its INITIAL `Message` (status SENT) sent `>= followup_delay_days` before `now`; no existing FOLLOWUP `Draft` (any status) and no FOLLOWUP `Message`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_followup_service.py`:

```python
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from app.draft.resume_schema import load_resume
from app.followup.claude_followup import FollowupPlan, MalformedFollowupError
from app.followup.service import FollowupResult, draft_followups
from app.models import (
    Contact, Draft, DraftMode, DraftStatus, Event, Message, MessageStatus,
    MessageType, Startup, StartupStatus,
)

FIXTURE = Path(__file__).parent / "fixtures" / "resume_min.yaml"
NOW = datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc)


def _resume():
    return load_resume(FIXTURE)


def _settings(**over):
    base = dict(followup_delay_days=5)
    base.update(over)
    return types.SimpleNamespace(**base)


def _gen(startup, resume, subject, body):
    return FollowupPlan(body="Just bumping this up.")


def _sent_startup(session, name, domain, *, sent_at, status=StartupStatus.SENT):
    s = Startup(name=name, domain=domain, source="yc", status=status)
    session.add(s); session.commit()
    c = Contact(startup_id=s.id, name="C", role="CTO", email=f"c@{domain}",
                found_via="scraped", confidence=0.9, verified=True)
    session.add(c); session.commit()
    d = Draft(startup_id=s.id, contact_id=c.id, type=MessageType.INITIAL,
              mode=DraftMode.FORMAL, subject="Intern application", body="Hello",
              status=DraftStatus.APPROVED)
    session.add(d); session.commit()
    session.add(Message(draft_id=d.id, type=MessageType.INITIAL, sent_at=sent_at,
                        smtp_message_id="<init@m>", status=MessageStatus.SENT))
    session.commit()
    return s, c, d


def test_followup_drafts_silent_startup(session):
    s, c, d = _sent_startup(session, "A", "a.io", sent_at=NOW - timedelta(days=6))
    results = draft_followups(session, resume=_resume(), now=NOW,
                              settings=_settings(), generator=_gen)
    assert results == [FollowupResult(s.id, True)]
    fu = session.scalars(
        select(Draft).where(Draft.type == MessageType.FOLLOWUP)).one()
    assert fu.subject == "Re: Intern application"
    assert fu.mode == DraftMode.CASUAL and fu.resume_pdf_path is None
    assert fu.status == DraftStatus.PENDING_REVIEW
    assert fu.contact_id == c.id
    assert fu.body == "Just bumping this up."


def test_followup_skips_too_recent(session):
    _sent_startup(session, "B", "b.io", sent_at=NOW - timedelta(days=3))
    results = draft_followups(session, resume=_resume(), now=NOW,
                              settings=_settings(), generator=_gen)
    assert results == []
    assert session.scalars(
        select(Draft).where(Draft.type == MessageType.FOLLOWUP)).all() == []


def test_followup_idempotent(session):
    _sent_startup(session, "C", "c.io", sent_at=NOW - timedelta(days=6))
    draft_followups(session, resume=_resume(), now=NOW, settings=_settings(),
                    generator=_gen)
    second = draft_followups(session, resume=_resume(), now=NOW,
                             settings=_settings(), generator=_gen)
    assert second == []
    assert len(session.scalars(
        select(Draft).where(Draft.type == MessageType.FOLLOWUP)).all()) == 1


def test_followup_skips_non_sent_status(session):
    _sent_startup(session, "R", "r.io", sent_at=NOW - timedelta(days=6),
                  status=StartupStatus.REPLIED)
    results = draft_followups(session, resume=_resume(), now=NOW,
                              settings=_settings(), generator=_gen)
    assert results == []


def test_followup_provider_error_contained(session):
    s, c, d = _sent_startup(session, "E", "e.io", sent_at=NOW - timedelta(days=6))

    def boom(startup, resume, subject, body):
        raise MalformedFollowupError("bad")

    results = draft_followups(session, resume=_resume(), now=NOW,
                              settings=_settings(), generator=boom)
    assert results == [FollowupResult(s.id, False)]
    assert session.scalars(
        select(Draft).where(Draft.type == MessageType.FOLLOWUP)).all() == []
    assert session.scalars(
        select(Event).where(Event.kind == "followup_failed")).all()


def test_followup_dry_run_persists_nothing(session):
    s, c, d = _sent_startup(session, "D", "d.io", sent_at=NOW - timedelta(days=6))
    results = draft_followups(session, resume=_resume(), now=NOW,
                              settings=_settings(), generator=_gen, dry_run=True)
    assert results == [FollowupResult(s.id, True)]
    assert session.scalars(
        select(Draft).where(Draft.type == MessageType.FOLLOWUP)).all() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_followup_service.py -v`
Expected: FAIL — `ModuleNotFoundError: app.followup.service`.

- [ ] **Step 3: Implement the service**

Create `app/followup/service.py`:

```python
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import anthropic
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.followup.claude_followup import (
    FollowupPlan, MalformedFollowupError, followup_plan,
)
from app.models import (
    Draft, DraftMode, DraftStatus, Event, Message, MessageStatus, MessageType,
    Startup, StartupStatus,
)


@dataclass
class FollowupResult:
    startup_id: int
    drafted: bool


def _as_utc(dt: datetime) -> datetime:
    """SQLite round-trips DateTime(timezone=True) as tz-naive; pin it to UTC
    before comparing to an aware now (we always store UTC)."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _initial_draft(session, startup_id):
    return session.scalars(
        select(Draft)
        .where(Draft.startup_id == startup_id, Draft.type == MessageType.INITIAL)
        .order_by(Draft.id).limit(1)).first()


def _initial_sent_at(session, startup_id):
    return session.scalar(
        select(func.max(Message.sent_at))
        .join(Draft, Message.draft_id == Draft.id)
        .where(Draft.startup_id == startup_id,
               Message.type == MessageType.INITIAL,
               Message.status == MessageStatus.SENT))


def _has_followup(session, startup_id) -> bool:
    has_draft = session.scalar(
        select(Draft.id).where(Draft.startup_id == startup_id,
                               Draft.type == MessageType.FOLLOWUP)) is not None
    has_msg = session.scalar(
        select(Message.id).join(Draft, Message.draft_id == Draft.id)
        .where(Draft.startup_id == startup_id,
               Message.type == MessageType.FOLLOWUP)) is not None
    return has_draft or has_msg


def _log_failed(session, startup_id, reason, **extra):
    session.add(Event(startup_id=startup_id, kind="followup_failed",
                      payload={"reason": reason, **extra}))
    session.commit()


def draft_followups(session: Session, *, resume, now, settings,
                    generator=followup_plan, limit: int = 50,
                    dry_run: bool = False) -> list[FollowupResult]:
    cutoff = now - timedelta(days=settings.followup_delay_days)
    results: list[FollowupResult] = []
    made = 0
    for s in session.scalars(
            select(Startup).where(Startup.status == StartupStatus.SENT)
            .order_by(Startup.id)).all():
        if made >= limit:
            break
        if _has_followup(session, s.id):
            continue
        sent_at = _initial_sent_at(session, s.id)
        if sent_at is None or _as_utc(sent_at) > cutoff:
            continue
        initial = _initial_draft(session, s.id)
        if initial is None:
            continue
        sid = s.id
        try:
            plan: FollowupPlan = generator(s, resume, initial.subject, initial.body)
        except (MalformedFollowupError, anthropic.AnthropicError, ValueError) as exc:
            session.rollback()
            if not dry_run:
                _log_failed(session, sid, "provider_error", detail=str(exc))
            results.append(FollowupResult(sid, False))
            continue
        if not dry_run:
            subj = initial.subject if initial.subject.lower().startswith("re:") \
                else "Re: " + initial.subject
            session.add(Draft(
                startup_id=sid, contact_id=initial.contact_id,
                type=MessageType.FOLLOWUP, mode=DraftMode.CASUAL,
                subject=subj, body=plan.body, resume_pdf_path=None,
                status=DraftStatus.PENDING_REVIEW))
            session.add(Event(startup_id=sid, kind="followup_drafted", payload={}))
            session.commit()
        results.append(FollowupResult(sid, True))
        made += 1
    return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_followup_service.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add app/followup/service.py tests/test_followup_service.py
git commit -m "feat: draft_followups service (eligibility, idempotency, dry-run)"
```

---

## Task 4: Threaded follow-up sending

**Files:**
- Modify: `app/send/smtp_client.py` (`build_email`, ~line 9-23)
- Modify: `app/send/service.py` (`_eligible_drafts` ~line 60-70; `_send_one` ~line 96-144)
- Test: `tests/test_send_followup.py`

**Interfaces:**
- Consumes: `Draft.type` (Task 1); `Message.smtp_message_id`, `MessageType` (existing).
- Produces:
  - `build_email(..., in_reply_to: str | None = None, references: str | None = None)` — sets those headers when truthy.
  - `_eligible_drafts` excludes drafts that already have **any** `Message`.
  - `_send_one` threads a FOLLOWUP draft against its startup's INITIAL send and records `Message(type=draft.type)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_send_followup.py`:

```python
import types
from datetime import datetime, timezone

from sqlalchemy import select

from app.models import (
    Contact, Draft, DraftMode, DraftStatus, Message, MessageStatus,
    MessageType, Startup, StartupStatus,
)
from app.send.service import send_batch
from app.send.smtp_client import build_email

NOW = datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc)


def _settings(**over):
    base = dict(from_email="me@d.com", from_name="Me", smtp_user="me@d.com",
                test_recipient="me@d.com", send_start="00:00", send_end="23:59",
                send_timezone="Asia/Kolkata", daily_cap=30, ramp_daily_cap=15,
                ramp_days=7)
    base.update(over)
    return types.SimpleNamespace(**base)


class _Recorder:
    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)
        return msg["Message-ID"]


def test_build_email_sets_threading_headers():
    msg = build_email(from_email="me@d.com", from_name="Me", to="x@y.io",
                      subject="Re: Hi", body="bump", pdf_path=None,
                      in_reply_to="<init@m>", references="<init@m>")
    assert msg["In-Reply-To"] == "<init@m>"
    assert msg["References"] == "<init@m>"


def test_build_email_no_threading_by_default():
    msg = build_email(from_email="me@d.com", from_name="Me", to="x@y.io",
                      subject="Hi", body="hello", pdf_path=None)
    assert msg["In-Reply-To"] is None
    assert msg["References"] is None


def _followup_ready(session, name, domain, init_msg_id="<init@m>"):
    s = Startup(name=name, domain=domain, source="yc", status=StartupStatus.QUEUED)
    session.add(s); session.commit()
    c = Contact(startup_id=s.id, name="C", role="CTO", email=f"c@{domain}",
                found_via="scraped", confidence=0.9, verified=True)
    session.add(c); session.commit()
    init = Draft(startup_id=s.id, contact_id=c.id, type=MessageType.INITIAL,
                 mode=DraftMode.FORMAL, subject="Intern application", body="Hello",
                 status=DraftStatus.APPROVED)
    session.add(init); session.commit()
    session.add(Message(draft_id=init.id, type=MessageType.INITIAL, sent_at=NOW,
                        smtp_message_id=init_msg_id, status=MessageStatus.SENT))
    fu = Draft(startup_id=s.id, contact_id=c.id, type=MessageType.FOLLOWUP,
               mode=DraftMode.CASUAL, subject="Re: Intern application",
               body="bump", resume_pdf_path=None, status=DraftStatus.APPROVED)
    session.add(fu); session.commit()
    return s, init, fu


def test_send_followup_threads_and_types(session):
    s, init, fu = _followup_ready(session, "A", "a.io")
    t = _Recorder()
    results = send_batch(session, now=NOW, transport=t, settings=_settings(),
                         force=True)
    assert results[0].sent is True and results[0].draft_id == fu.id
    sent_msg = t.sent[0]
    assert sent_msg["In-Reply-To"] == "<init@m>"
    assert sent_msg["References"] == "<init@m>"
    assert sent_msg["Subject"] == "Re: Intern application"
    fu_msg = session.scalars(
        select(Message).where(Message.type == MessageType.FOLLOWUP)).one()
    assert fu_msg.status == MessageStatus.SENT
    assert s.status == StartupStatus.SENT


def test_sent_initial_not_re_selected_only_followup_sends(session):
    s, init, fu = _followup_ready(session, "B", "b.io")
    t = _Recorder()
    send_batch(session, now=NOW, transport=t, settings=_settings(),
               force=True, limit=5)
    assert len(t.sent) == 1
    assert t.sent[0]["Subject"] == "Re: Intern application"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_send_followup.py -v`
Expected: FAIL — `build_email` rejects `in_reply_to`, and the follow-up send does not set threading headers.

- [ ] **Step 3: Add threading params to `build_email`**

In `app/send/smtp_client.py`, replace the `build_email` signature + body so it accepts and sets the two headers:

```python
def build_email(*, from_email: str, from_name: str, to: str, subject: str,
                body: str, pdf_path: str | None,
                in_reply_to: str | None = None,
                references: str | None = None) -> EmailMessage:
    """Build a plain-text email; attach the PDF when pdf_path is set.
    Sets a generated Message-ID header for later reply matching. When
    in_reply_to/references are given, threads the mail onto that Message-ID."""
    msg = EmailMessage()
    msg["From"] = formataddr((from_name, from_email)) if from_name else from_email
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid()
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    msg.set_content(body)
    if pdf_path:
        data = Path(pdf_path).read_bytes()
        msg.add_attachment(data, maintype="application", subtype="pdf",
                           filename=Path(pdf_path).name)
    return msg
```

- [ ] **Step 4: Generalize `_eligible_drafts`**

In `app/send/service.py`, change the exclusion subquery in `_eligible_drafts` from INITIAL-only to any message:

```python
def _eligible_drafts(session: Session, limit: int) -> list[Draft]:
    return session.scalars(
        select(Draft)
        .join(Startup, Draft.startup_id == Startup.id)
        .where(Draft.status == DraftStatus.APPROVED,
               Startup.status == StartupStatus.QUEUED,
               Draft.id.not_in(select(Message.draft_id)))
        .order_by(Draft.id)
        .limit(limit)
    ).all()
```

- [ ] **Step 5: Thread + type in `_send_one`**

In `app/send/service.py`, inside `_send_one`, after the `to_addr` guard block (right before the `msg = build_email(...)` call), add the threading lookup:

```python
    in_reply_to = None
    if draft.type == MessageType.FOLLOWUP:
        in_reply_to = session.scalar(
            select(Message.smtp_message_id)
            .join(Draft, Message.draft_id == Draft.id)
            .where(Draft.startup_id == sid,
                   Message.type == MessageType.INITIAL,
                   Message.smtp_message_id.is_not(None))
            .order_by(Message.id).limit(1))
```

Then pass the headers into `build_email` (add the two kwargs to the existing call):

```python
    msg = build_email(
        from_email=settings.from_email or settings.smtp_user,
        from_name=settings.from_name, to=to_addr, subject=draft.subject,
        body=draft.body,
        pdf_path=draft.resume_pdf_path if draft.mode == DraftMode.FORMAL else None,
        in_reply_to=in_reply_to, references=in_reply_to)
```

And change the persisted `Message` type from hard-coded INITIAL to the draft's type (the `session.add(Message(...))` line after a real send):

```python
    session.add(Message(draft_id=did, type=draft.type, sent_at=now,
                        smtp_message_id=message_id, status=MessageStatus.SENT))
```

(`select`, `Message`, `Draft`, `MessageType` are already imported in this file.)

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_send_followup.py -v`
Expected: PASS (4 tests).

- [ ] **Step 7: Run send + full suite (no regressions)**

Run: `.venv/Scripts/python -m pytest tests/test_send_service.py tests/test_send_smtp.py -q && .venv/Scripts/python -m pytest -q`
Expected: all pass (initial sends still set neither threading header; idempotency unchanged since a sent initial now still has a Message).

- [ ] **Step 8: Commit**

```bash
git add app/send/smtp_client.py app/send/service.py tests/test_send_followup.py
git commit -m "feat: thread follow-up sends onto the initial Message-ID"
```

---

## Task 5: `no_response` sweep guard

**Files:**
- Modify: `app/inbox/service.py` (`_sweep_no_response`, ~line 103-117; imports line 8-11)
- Test: `tests/test_sweep_followup.py`

**Interfaces:**
- Consumes: `Draft.type` (Task 1); models `Draft, DraftStatus, MessageType` (add `DraftStatus, MessageType` to the existing import).
- Produces: `_sweep_no_response` skips a `SENT` startup that has a FOLLOWUP `Draft` in `PENDING_REVIEW`/`APPROVED` with no `Message` yet. Give-up timing (from newest send) is unchanged.

- [ ] **Step 1: Write the failing test**

Create `tests/test_sweep_followup.py`:

```python
from datetime import datetime, timezone

from app.inbox.service import _sweep_no_response
from app.models import (
    Draft, DraftMode, DraftStatus, Message, MessageStatus, MessageType,
    Startup, StartupStatus,
)


def _utc(y, mo, d):
    return datetime(y, mo, d, 12, 0, tzinfo=timezone.utc)


def _startup(session, name, *, initial_at, followup_at=None,
             followup_draft_status=None):
    s = Startup(name=name, domain=name + ".io", source="yc",
                status=StartupStatus.SENT)
    session.add(s); session.commit()
    init = Draft(startup_id=s.id, type=MessageType.INITIAL, mode=DraftMode.FORMAL,
                 subject="Hi", body="x", status=DraftStatus.APPROVED)
    session.add(init); session.commit()
    session.add(Message(draft_id=init.id, type=MessageType.INITIAL,
                        sent_at=initial_at, smtp_message_id="<i>",
                        status=MessageStatus.SENT))
    session.commit()
    if followup_at is not None:
        fu = Draft(startup_id=s.id, type=MessageType.FOLLOWUP,
                   mode=DraftMode.CASUAL, subject="Re: Hi", body="bump",
                   status=DraftStatus.APPROVED)
        session.add(fu); session.commit()
        session.add(Message(draft_id=fu.id, type=MessageType.FOLLOWUP,
                            sent_at=followup_at, smtp_message_id="<f>",
                            status=MessageStatus.SENT))
        session.commit()
    if followup_draft_status is not None:
        fu = Draft(startup_id=s.id, type=MessageType.FOLLOWUP,
                   mode=DraftMode.CASUAL, subject="Re: Hi", body="bump",
                   status=followup_draft_status)
        session.add(fu); session.commit()
    return s


def test_sweep_clock_measures_from_followup(session):
    s = _startup(session, "A", initial_at=_utc(2026, 8, 1),
                 followup_at=_utc(2026, 8, 6))
    # Aug 15 is 14 days after the initial, but only 9 after the follow-up → keep
    assert _sweep_no_response(session, _utc(2026, 8, 15), 14, mutate=True) == 0
    assert s.status == StartupStatus.SENT
    # Aug 20 is 14 days after the follow-up → give up
    assert _sweep_no_response(session, _utc(2026, 8, 20), 14, mutate=True) == 1
    assert s.status == StartupStatus.NO_RESPONSE


def test_sweep_skips_pending_followup(session):
    s = _startup(session, "B", initial_at=_utc(2026, 8, 1),
                 followup_draft_status=DraftStatus.PENDING_REVIEW)
    assert _sweep_no_response(session, _utc(2026, 8, 20), 14, mutate=True) == 0
    assert s.status == StartupStatus.SENT


def test_sweep_gives_up_on_rejected_followup(session):
    s = _startup(session, "C", initial_at=_utc(2026, 8, 1),
                 followup_draft_status=DraftStatus.REJECTED)
    assert _sweep_no_response(session, _utc(2026, 8, 20), 14, mutate=True) == 1
    assert s.status == StartupStatus.NO_RESPONSE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_sweep_followup.py -v`
Expected: FAIL — `test_sweep_skips_pending_followup` sweeps the startup (returns 1) because no guard exists yet.

- [ ] **Step 3: Extend the imports**

In `app/inbox/service.py`, change the models import (currently
`Contact, Draft, Event, InboxKind, InboxMessage, Message, MessageStatus, ReplyLabel, Startup, StartupStatus`)
to also include `DraftStatus` and `MessageType`:

```python
from app.models import (
    Contact, Draft, DraftStatus, Event, InboxKind, InboxMessage, Message,
    MessageStatus, MessageType, ReplyLabel, Startup, StartupStatus,
)
```

- [ ] **Step 4: Add the guard helper + use it in the sweep**

In `app/inbox/service.py`, add a helper just above `_sweep_no_response`:

```python
def _has_inflight_followup(session, startup_id) -> bool:
    """A follow-up drafted but not yet sent (awaiting review or sending)."""
    return session.scalar(
        select(Draft.id).where(
            Draft.startup_id == startup_id,
            Draft.type == MessageType.FOLLOWUP,
            Draft.status.in_((DraftStatus.PENDING_REVIEW, DraftStatus.APPROVED)),
            Draft.id.not_in(select(Message.draft_id)))) is not None
```

Then, inside `_sweep_no_response`, skip such startups — add the guard as the first line of the loop body:

```python
    for s in session.scalars(
            select(Startup).where(Startup.status == StartupStatus.SENT)).all():
        if _has_inflight_followup(session, s.id):
            continue
        newest = _newest_sent_at(session, s.id)
        if newest is None:
            continue
        if _as_utc(newest) < cutoff:
            due.append(s)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_sweep_followup.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Run inbox suite (no regressions)**

Run: `.venv/Scripts/python -m pytest tests/test_inbox_service.py -q`
Expected: all pass (startups without follow-up drafts are unaffected).

- [ ] **Step 7: Commit**

```bash
git add app/inbox/service.py tests/test_sweep_followup.py
git commit -m "feat: no_response sweep spares startups with an in-flight follow-up"
```

---

## Task 6: CLI command + docs

**Files:**
- Modify: `app/cli.py` (imports; add `_build_followup_generator` + `followups` command)
- Modify: `.env.example`
- Modify: `README.md`
- Test: `tests/test_cli_followups.py`

**Interfaces:**
- Consumes: `draft_followups`, `followup_plan` (Tasks 2-3); `load_resume`, `resolve_backend` (existing).
- Produces: `m2s followups [--limit N] [--dry-run]`; module-level `_build_followup_generator(settings)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_followups.py`:

```python
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from typer.testing import CliRunner

import app.cli as cli_mod
from app.cli import app
from app.db import get_engine, init_db, make_session
from app.followup.claude_followup import FollowupPlan
from app.models import (
    Contact, Draft, DraftMode, DraftStatus, Message, MessageStatus,
    MessageType, Startup, StartupStatus,
)

runner = CliRunner()
FIXTURE = Path(__file__).parent / "fixtures" / "resume_min.yaml"


def _prepare(monkeypatch, tmp_path):
    db = tmp_path / "test.db"
    monkeypatch.setenv("M2S_DB_PATH", str(db))
    monkeypatch.setenv("M2S_RESUME_PATH", str(FIXTURE))
    runner.invoke(app, ["init-db"])
    return db


def _seed_silent(db):
    engine = get_engine(db)
    init_db(engine)
    with make_session(engine) as s:
        st = Startup(name="A", domain="a.io", source="yc",
                     status=StartupStatus.SENT)
        s.add(st); s.commit()
        c = Contact(startup_id=st.id, name="C", role="CTO", email="c@a.io",
                    found_via="scraped", confidence=0.9, verified=True)
        s.add(c); s.commit()
        d = Draft(startup_id=st.id, contact_id=c.id, type=MessageType.INITIAL,
                  mode=DraftMode.FORMAL, subject="Intern application",
                  body="Hello", status=DraftStatus.APPROVED)
        s.add(d); s.commit()
        old = datetime.now(timezone.utc) - timedelta(days=10)
        s.add(Message(draft_id=d.id, type=MessageType.INITIAL, sent_at=old,
                      smtp_message_id="<i@m>", status=MessageStatus.SENT))
        s.commit()


def _fake_gen(settings):
    return lambda startup, resume, subject, body: FollowupPlan(body="bump")


def test_followups_drafts(monkeypatch, tmp_path):
    db = _prepare(monkeypatch, tmp_path)
    _seed_silent(db)
    monkeypatch.setattr(cli_mod, "_build_followup_generator", _fake_gen)
    out = runner.invoke(app, ["followups"])
    assert out.exit_code == 0 and "drafted=1" in out.output
    with make_session(get_engine(db)) as s:
        fu = s.scalars(
            select(Draft).where(Draft.type == MessageType.FOLLOWUP)).one()
        assert fu.subject == "Re: Intern application"
        assert fu.status == DraftStatus.PENDING_REVIEW


def test_followups_dry_run_writes_nothing(monkeypatch, tmp_path):
    db = _prepare(monkeypatch, tmp_path)
    _seed_silent(db)
    monkeypatch.setattr(cli_mod, "_build_followup_generator", _fake_gen)
    out = runner.invoke(app, ["followups", "--dry-run"])
    assert out.exit_code == 0 and "drafted=1" in out.output
    with make_session(get_engine(db)) as s:
        assert s.scalars(
            select(Draft).where(Draft.type == MessageType.FOLLOWUP)).all() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_cli_followups.py -v`
Expected: FAIL — no `followups` command (`SystemExit`/exit_code 2 with "No such command").

- [ ] **Step 3: Add imports + generator builder**

In `app/cli.py`, add these imports near the other `app.*` imports (after the `from app.draft...` lines):

```python
from app.followup.claude_followup import followup_plan
from app.followup.service import draft_followups
```

Add the generator builder next to `_build_classifier` (after it):

```python
def _build_followup_generator(settings):
    from app.draft.claude_draft import resolve_backend
    client, model = resolve_backend()
    return lambda startup, resume, subject, body: followup_plan(
        startup, resume, subject, body, client=client, model=model)
```

- [ ] **Step 4: Add the `followups` command**

In `app/cli.py`, add the command (place it after the `inbox` command):

```python
@app.command()
def followups(
    limit: int = typer.Option(50, help="Max follow-up drafts to create this run"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview; write nothing"),
):
    """Draft one threaded follow-up per silent startup (past the delay window)."""
    settings = get_settings()
    try:
        resume = load_resume(settings.resume_path)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    try:
        generator = _build_followup_generator(settings)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    now = datetime.now(timezone.utc)
    with _session() as session:
        results = draft_followups(session, resume=resume, now=now,
                                  settings=settings, generator=generator,
                                  limit=limit, dry_run=dry_run)
    drafted = sum(1 for r in results if r.drafted)
    typer.echo(f"followups: processed={len(results)} drafted={drafted}")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_cli_followups.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Update `.env.example`**

Add to `.env.example`, in the IMAP/inbox block area (near `M2S_NO_RESPONSE_DAYS` if present, otherwise after the IMAP settings):

```
# Follow-ups (Phase 6)
M2S_FOLLOWUP_DELAY_DAYS=5
```

- [ ] **Step 7: Update `README.md`**

Add a "Follow-ups (Phase 6)" section after the inbox section:

```markdown
## Follow-ups (Phase 6)

Each startup gets at most **one** follow-up. Once its initial email is
`M2S_FOLLOWUP_DELAY_DAYS` old (default 5) and it has not replied or bounced,
generate a short, Claude-drafted, threaded nudge:

    m2s followups              # draft follow-ups for eligible silent startups
    m2s followups --dry-run    # preview counts; write nothing

Follow-ups reuse the normal review/send flow — approve and send them exactly
like initial drafts:

    m2s approve <id>
    m2s send

The follow-up threads onto the original email (`In-Reply-To`/`References`) with
a `Re: <subject>` subject and no resume attachment. A startup is swept to
`no_response` 14 days after its **most recent** outbound message
(`M2S_NO_RESPONSE_DAYS`), so a followed-up startup is given up ~14 days after
the follow-up, not the initial send. A startup whose follow-up is still awaiting
review is never swept early.
```

- [ ] **Step 8: Run the full suite**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add app/cli.py .env.example README.md tests/test_cli_followups.py
git commit -m "feat: m2s followups command + docs"
```

---

## Self-Review

**Spec coverage:**
- Policy: one follow-up (idempotency in Task 3), day-5 delay (`followup_delay_days`, Tasks 1/3), no reply/bounce (SENT-only filter, Task 3) ✅
- Content: Claude-drafted, grounded, 2-3 lines (Task 2) ✅
- Threading: `In-Reply-To`/`References` to initial Message-ID, `Re:` subject, no PDF (Tasks 3/4) ✅
- Review gate reuse: `pending_review` → `approve` → `send` (Tasks 3/4, no new commands) ✅
- Give-up clock = 14 days after newest send + pending-follow-up guard (Task 5) ✅
- Config/CLI/docs (Tasks 1/6) ✅

**Placeholder scan:** No TBD/TODO/"handle edge cases"; every code step is verbatim.

**Type consistency:** `followup_plan(startup, resume, original_subject, original_body, *, client, model)` is called identically in the service (`generator(s, resume, initial.subject, initial.body)`) and the CLI builder. `draft_followups(session, *, resume, now, settings, generator, limit, dry_run)` matches the CLI call. `_eligible_drafts` now excludes any-message drafts, consumed by `_send_one` which sets `Message(type=draft.type)`. Enum columns use `values_callable`. All imports named exist.

**Note on `MessageType` reuse:** `Draft.type` and `Message.type` share the enum (`INITIAL`/`FOLLOWUP`); a follow-up Draft produces a follow-up Message of the same type — intentional, not a collision.
