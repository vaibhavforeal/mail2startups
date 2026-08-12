# Phase 4 — Sending Engine — Design Spec

**Date:** 2026-08-12
**Status:** Approved by user (brainstorming session)
**Parent spec:** `2026-08-11-mail2startups-design.md` (§ Sending & scheduling, § Deliverability & safety rails)

## Scope

Turn `approved` drafts into real emails leaving the user's Hostinger mailbox,
drip-paced and safe, driven entirely from the CLI. This phase builds the
`app/send/` module, an approval workflow, a one-shot send command, pacing
(send window + daily cap + week-1 ramp), a persistent pause/failure state, and
the deliverability rails (DNS preflight + test sends to the user's own inbox).

The models already carry everything downstream needs: `Message`,
`MessageType`, `MessageStatus`, `DraftStatus` (pending_review / approved /
rejected), and `StartupStatus` (queued / sent). No schema change is needed
except one new singleton table for campaign state.

**Explicitly deferred** (out of this phase): the day-5–7 follow-up (needs
inbox reply/bounce detection), the APScheduler daemon, the web dashboard, and
IMAP inbox tracking.

## Requirements

- **Approval gate:** nothing sends unless a human approved the draft. Approval
  is a CLI action moving `Draft` → `approved` and `Startup` → `queued`.
- **One-shot send:** `m2s send` sends the next-due email(s) respecting pause,
  window, daily cap, and ramp, then exits. No long-running process, no
  in-process sleeping — pacing between sends is externalized to the OS
  scheduler (Windows Task Scheduler / cron) running the command periodically.
- **Idempotent + status-gated**, exactly like every existing stage: a send only
  processes an `approved` draft whose startup is `queued` and which has no prior
  `initial` `Message`. Re-running never double-sends.
- **Deterministic pacing:** window, cap, and ramp are pure functions of a
  passed-in `now` and DB state — no wall-clock read inside them, so tests are
  hermetic.
- **Fully offline tests:** the SMTP transport and the DNS resolver are injected;
  no test opens a socket. (Same discipline as the injected Claude client.)
- **Error containment:** SMTP/network failures are contained per-send with
  `(smtplib.SMTPException, OSError)` — never a bare `except`. Three consecutive
  failures auto-pause the campaign.
- **Deliverability first:** a DNS preflight (SPF / DKIM / DMARC) and a 5-email
  test-send to the user's own inbox exist before the first real send.
- **One new dependency:** `dnspython` (TXT record lookups for preflight). Nothing
  else added.
- **Secrets stay out of git:** SMTP credentials live in `.env` (gitignored),
  documented blank in `.env.example`.

## Decisions

| Topic | Decision | Why |
|---|---|---|
| Run model | One-shot `m2s send`, default `--limit 1` | User's choice. Pure function of DB + clock; testable; "machine-off catch-up" and the daily cap both fall out for free. The randomized 15–30 min gap lives in the OS schedule, not the engine. |
| Pacing gaps | Externalized to Task Scheduler cadence | Keeps the engine sleepless and deterministic. A future daemon can wrap `send_batch` with sleeps. |
| Reject behavior | `reject` → `Draft.rejected`, `Startup.dead` | No auto-regenerate in this phase; rejecting ends that startup's path, preserved in history (nothing deleted). Re-drafting is a later, explicit action. |
| DKIM selector | Configurable `M2S_DKIM_SELECTOR` | Hostinger shows the user their selector in the DNS panel; hard-coding a guess would produce false preflight failures. |
| Campaign state | New singleton `CampaignState` table | Centralizes pause flag, consecutive-failure counter, and the ramp anchor (`first_send_at`) in one auditable place. |
| Dry-run semantics | Real SMTP send, recipient rewritten to `test_recipient`, no status mutation | Matches parent spec ("`--dry-run` routes real SMTP sends to the user's own address"); confirms the full send path without touching a startup's state. |
| Test transport | Injected transport object with `.send(mime) -> message_id` | Mirrors the drafter/renderer injection pattern; keeps tests offline. |

## Architecture

New module `app/send/`, one new model, config additions, new CLI commands, one
new test file per unit. No existing module is restructured.

```
app/send/
├── __init__.py
├── smtp_client.py   # build_email() + SmtpTransport (SMTP_SSL) + Transport protocol
├── pacing.py        # is_within_window / effective_daily_cap / sent_today / budget_remaining
├── state.py         # CampaignState accessors: ensure/pause/resume/record_success/record_failure
├── preflight.py     # check_dns(domain, resolver) -> PreflightReport
└── service.py       # approve_drafts / reject_drafts / send_batch / test_send
```

### `app/models.py` — one new table

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

`init_db` already runs `create_all`, so the table appears on next init — no
migration tooling needed (local, re-creatable DB).

### `app/config.py` — sending settings (all `M2S_`-prefixed)

```python
smtp_host: str = "smtp.hostinger.com"
smtp_port: int = 465
smtp_user: str = ""            # mailbox login / address
smtp_password: str = ""
from_email: str = ""           # defaults to smtp_user when blank
from_name: str = ""            # display name
test_recipient: str = ""       # own address for --dry-run and test-send
send_start: str = "09:30"      # IST, HH:MM
send_end: str = "18:30"        # IST, HH:MM
send_timezone: str = "Asia/Kolkata"
daily_cap: int = 30
ramp_daily_cap: int = 15       # cap during the ramp window
ramp_days: int = 7             # days from first_send_at that the ramp applies
dkim_selector: str = ""        # from the user's Hostinger DNS panel
```

### `app/send/smtp_client.py`

```python
from email.message import EmailMessage
from typing import Protocol

def build_email(*, from_email: str, from_name: str, to: str, subject: str,
                body: str, pdf_path: str | None) -> EmailMessage:
    """Plain-text email; attach the PDF (application/pdf) when pdf_path is set.
    Sets a generated Message-ID via email.utils.make_msgid()."""

class Transport(Protocol):
    def send(self, msg: EmailMessage) -> str: ...   # returns the Message-ID

class SmtpTransport:
    """Real transport: SMTP_SSL(host, port) → login → send_message. Not used in tests."""
    def __init__(self, host, port, user, password): ...
    def send(self, msg: EmailMessage) -> str: ...
```

### `app/send/pacing.py` — pure, `now` injected

```python
def is_within_window(now, *, start_hhmm, end_hhmm, tz) -> bool:
    """True on Mon–Fri when the tz-local time is within [start, end]."""

def effective_daily_cap(now, first_send_at, *, daily_cap, ramp_cap, ramp_days, tz) -> int:
    """ramp_cap while now is within ramp_days of first_send_at (or first_send_at is None),
    else daily_cap."""

def sent_today(session, now, *, tz) -> int:
    """Count messages with status SENT whose sent_at falls on today's tz-local date."""

def budget_remaining(session, now, state, settings) -> int:
    """max(0, effective_daily_cap(...) - sent_today(...))."""
```

### `app/send/service.py` — the orchestrator

```python
@dataclass
class SendResult:
    draft_id: int
    sent: bool
    reason: str | None      # skip/failure reason when not sent

def approve_drafts(session, ids=None, *, all_pending=False) -> int
def reject_drafts(session, ids) -> int

def send_batch(session, *, now, transport, settings, limit=1,
               dry_run=False, force=False) -> list[SendResult]:
    """Select up to `limit` approved drafts whose startup is QUEUED and which
    have no initial Message. Gating:
      • paused blocks real sends; a dry_run is always allowed (it mutates nothing).
      • window and budget are enforced for real sends, and both are bypassed when
        dry_run or force is set.
    For each eligible draft:
      • build_email (dry_run rewrites `to` → settings.test_recipient)
      • transport.send(msg)
      • on success (non-dry-run): Message(type=INITIAL, sent_at=now,
        smtp_message_id, status=SENT); Startup→SENT; Event 'sent';
        state.record_success(); set first_send_at if unset.
      • on success (dry_run): Event 'dry_run_send'; no status mutation.
      • on (SMTPException, OSError): Event 'send_failed'; state.record_failure();
        if consecutive_failures >= 3 → state.pause('auto: 3 consecutive failures')."""

def test_send(session, *, transport, settings, count=5) -> int:
    """Send `count` canned emails to settings.test_recipient to confirm auth and
    inbox placement. Does not touch drafts/startups; logs an Event."""
```

### `app/send/preflight.py`

```python
@dataclass
class PreflightReport:
    spf: tuple[bool, str]     # (passed, detail)
    dkim: tuple[bool, str]
    dmarc: tuple[bool, str]
    @property
    def ok(self) -> bool: ...

def check_dns(domain, *, selector, resolve) -> PreflightReport:
    """resolve(name, 'TXT') -> list[str]. SPF: TXT on domain containing 'v=spf1'.
    DMARC: TXT on _dmarc.<domain> containing 'v=DMARC1'. DKIM: TXT on
    <selector>._domainkey.<domain> containing 'v=DKIM1' (skipped with a clear
    message when selector is blank)."""
```

`resolve` defaults to a thin `dnspython` wrapper in the CLI; tests pass a fake
returning canned TXT records.

## Data Flow

```
m2s approve 12 34          Draft→approved, Startup→queued            (Event 'approved')
m2s send                   send_batch(limit=1)
  └─ paused? ──yes─▶ skip (Event 'send_skipped: paused')
  └─ within window? ──no─▶ skip (unless --force/--dry-run)
  └─ budget_remaining>0? ──no─▶ skip (Event 'send_skipped: cap')
  └─ pick 1 approved+queued draft with no initial Message
       └─ build_email → transport.send → Message(SENT), Startup→SENT, Event 'sent'
       └─ on SMTP error → Event 'send_failed', failures++, maybe auto-pause
```

Status pipeline advanced this phase: `drafted → queued → sent` (via approve then
send). `rejected` drafts move their startup to `dead`.

## Error Handling

- **SMTP / network failure:** contained per-send with `(smtplib.SMTPException,
  OSError)`; logged to `events` as `send_failed`; increments the consecutive
  failure counter. **3 consecutive failures → auto-pause** (persisted); a later
  success resets the counter to 0.
- **Manual pause:** `m2s pause` sets `paused=True`; `m2s resume` clears it and
  resets the failure counter. `send_batch` refuses to send while paused.
- **Outside window / over cap / nothing eligible:** not errors — `send_batch`
  returns skip results with reasons; the command reports them and exits 0.
- **Malformed config (missing SMTP creds):** the CLI reports a clear error and
  exits non-zero before constructing a transport.
- No new bare excepts anywhere; nothing is auto-deleted.

## Testing

New files under `tests/`, all offline:

- `test_send_smtp.py` — `build_email` sets From/To/Subject, plain-text body, a
  Message-ID, and attaches the PDF only in formal mode; an injected transport
  records the sent `EmailMessage` (never opens a socket).
- `test_pacing.py` — `is_within_window` (inside, outside, weekend, boundary);
  `effective_daily_cap` (ramp vs steady, `first_send_at=None`); `sent_today`
  counts only today's IST-local SENT messages; `budget_remaining` floors at 0.
- `test_send_service.py` — approve moves draft+startup; reject → dead;
  `send_batch` sends an eligible draft and advances status; idempotent (no
  second send); respects pause and window; `--dry-run` sends to `test_recipient`
  and mutates nothing; SMTP error path logs `send_failed` and 3-in-a-row
  auto-pauses; `--force` bypasses the window.
- `test_preflight.py` — `check_dns` with an injected resolver: all-pass;
  SPF/DMARC missing → failure with detail; blank selector → DKIM skipped.
- `test_cli_send.py` — `approve`/`reject`/`send`/`preflight`/`test-send`/
  `pause`/`resume` wire correctly (Typer runner, injected transport/resolver,
  in-memory DB) and report sensible output.

No existing test changes behavior. The `dnspython` import lives only in the CLI
resolver wrapper and `preflight.py`; core logic takes the resolver as a parameter.

## Documentation

- `.env.example`: add an "SMTP / sending" block with the new `M2S_*` vars blank,
  plus a one-line note that `M2S_DKIM_SELECTOR` comes from the Hostinger DNS panel.
- `README.md`: a "Sending (Phase 4)" section — the `approve → send` flow, the
  one-shot model with a sample Windows Task Scheduler cadence, `preflight` and
  `test-send` before the first real campaign, and `--dry-run`.
- `requirements.txt` / pyproject: add `dnspython`.

## Out of Scope (YAGNI)

- The day-5–7 follow-up (depends on inbox reply/bounce detection).
- APScheduler daemon / any long-running process.
- IMAP inbox tracking, bounce parsing, reply classification.
- The web dashboard and review queue UI.
- Multi-mailbox rotation, open-tracking pixels, A/B copy testing.
