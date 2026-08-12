# Phase 5 — Inbox Detection (reply / bounce tracking) — Design Spec

**Date:** 2026-08-12
**Status:** Approved by user (brainstorming session)
**Parent spec:** `2026-08-11-mail2startups-design.md` (§ Inbox, replies & bounces, § Follow-ups)
**Builds on:** `2026-08-12-phase4-sending-design.md` (one-shot CLI + OS-scheduler model, injected-transport/offline-test discipline)

## Scope

Close the read side of the outbound loop. A one-shot `m2s inbox` command polls
the user's Hostinger IMAP mailbox, matches incoming mail to the outbound
`Message`s sent in Phase 4, and advances each startup's status accordingly:

- **Replies** → `Startup` `replied`, classified by Claude
  (interested / rejection / auto-reply / other), recorded in a dedicated
  `inbox_messages` table.
- **Bounces** → `Message` `bounced`, the bouncing contact demoted
  (`verified=False`), and `Startup` `bounced`. Re-targeting another contact is
  **not** done here — it belongs to the future re-send/follow-up phase, which
  owns draft/send re-entry (see Decisions).
- **Stale sends** → a `no_response` sweep moves a `sent` startup to
  `no_response` after `no_response_days` (default 14) with no reply or bounce.

This phase builds the `app/inbox/` module, one new model plus two enums, config
additions, and one new CLI command. No existing module is restructured.

**Explicitly deferred** (out of this phase, consistent with the project's
one-subsystem-per-phase cadence):

- The day-5–7 **follow-up** send (its own next phase; it depends on this
  detection existing). When it lands, the follow-up `Message` naturally pushes
  each startup's "newest SENT" forward, re-anchoring the `no_response` clock for
  free.
- The APScheduler daemon / any long-running poller.
- The web dashboard and review UI.
- Multi-mailbox rotation, richer thread reconstruction, open-tracking.

## Requirements

- **One-shot, no daemon:** `m2s inbox` polls once and exits, exactly like
  `m2s send`. The parent spec's "poll every ~10 min" is satisfied by an OS
  scheduler (Windows Task Scheduler / cron) invoking the command on a cadence —
  no in-process loop or sleep.
- **Read-only mailbox access:** fetch is incremental by IMAP **UID** above a
  stored watermark; the poller never sets `\Seen` and never mutates the user's
  mailbox. A UIDVALIDITY change resets the watermark safely.
- **Idempotent + status-gated**, like every existing stage: re-running never
  double-records a reply/bounce (dedup on the inbound email's own `Message-ID`)
  and never re-advances a startup already `replied`/`bounced`.
- **Exact-first matching:** replies are matched by `In-Reply-To`/`References`
  containing a stored `Message.smtp_message_id`; a `From`-address match is a
  fallback only. Bounces are matched by a MAILER-DAEMON/DSN envelope whose body
  carries one of our `smtp_message_id`s.
- **Deterministic sweep:** the `no_response` cutoff is a pure function of a
  passed-in `now` and DB state — no wall-clock read inside it, so tests are
  hermetic (same discipline as Phase 4 pacing).
- **Fully offline tests:** the IMAP client and the Claude classifier are
  injected; no test opens a socket. (Same discipline as the injected SMTP
  transport and DNS resolver.)
- **Error containment (specific tuples, never bare `except`):** IMAP/network
  failures contained with `(imaplib.IMAP4.error, OSError)`; Claude classify
  failures contained with `(anthropic.AnthropicError, ValueError)` and fall back
  to label `other` so a matched reply is never lost.
- **No new dependency:** `imaplib` is stdlib; `anthropic` is already declared.
- **Secrets stay out of git:** IMAP credentials live in `.env` (gitignored),
  documented blank in `.env.example`; they default to the SMTP credentials when
  blank.

## Decisions

| Topic | Decision | Why |
|---|---|---|
| Phase scope | Detection only — replies, bounces, `no_response`. Follow-ups next phase. | Follow-ups depend on detection; each phase stays independently shippable and testable. User's choice. |
| Run model | One-shot `m2s inbox`, OS-scheduler cadence | Matches Phase 4; keeps the poller sleepless and deterministic. |
| New-mail tracking | UID watermark in `CampaignState` (`last_imap_uid` + `imap_uidvalidity`), read-only fetch | Incremental and idempotent without touching `\Seen` or the user's read state; UIDVALIDITY guards mailbox resets. |
| Reply classification | Included now — a Claude call per matched reply (interested / rejection / auto-reply / other) | User's choice. Reuses the injected-Claude pattern from `app/draft/`; mocked offline in tests. |
| Bounce behavior | Mark `Message` `bounced` + demote the contact (`verified=False`) + `Startup` `bounced`. No re-target, no re-draft, no auto-resend from the inbox path. | User's choice (keep detection pure). The current `draft_startup` guard skips any startup that already has a `Draft`, and `select_primary_contact` would re-pick the bounced address — so real re-targeting requires `draft/` + contact-selection changes that belong to the re-send/follow-up phase, not this detection-only one. |
| Detail storage | Dedicated `inbox_messages` table (source of truth for inbound mail) + a thin `Event` transition row | User's choice. A queryable record of every reply/bounce; `Event` keeps the project-wide append-only timeline. |
| Idempotency key | `inbox_messages.imap_message_id` UNIQUE (inbound email's own Message-ID) | Stable across re-fetch; dedupes even if the UID watermark is reset. |
| `no_response` anchor | `now − no_response_days` vs the startup's newest SENT `Message.sent_at` | Works now from the initial send; the future follow-up send re-anchors it automatically (newest SENT moves forward). |

## Architecture

New module `app/inbox/`, one new model + two enums, config additions, one new
CLI command, one new test file per unit.

```
app/inbox/
├── __init__.py
├── imap_client.py   # ImapClient Protocol + HostingerImap (imaplib IMAP4_SSL) + FetchedMessage
├── matching.py      # pure: match_reply() / detect_bounce() against stored smtp_message_ids
├── classify.py      # classify_reply(client, text) -> ReplyLabel   (injected Claude client)
└── service.py       # poll_inbox(session, *, imap, classifier, now, settings) -> InboxResult
```

### `app/models.py` — one new table, two new enums

```python
class InboxKind(str, enum.Enum):
    REPLY = "reply"
    BOUNCE = "bounce"

class ReplyLabel(str, enum.Enum):
    INTERESTED = "interested"
    REJECTION = "rejection"
    AUTO_REPLY = "auto_reply"
    OTHER = "other"

class InboxMessage(Base):
    __tablename__ = "inbox_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    startup_id: Mapped[int] = mapped_column(ForeignKey("startups.id"))
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id"), nullable=True)      # outbound Message it replies to / bounces
    kind: Mapped[InboxKind] = mapped_column(
        Enum(InboxKind, values_callable=lambda e: [m.value for m in e]))
    imap_message_id: Mapped[str] = mapped_column(String(255), unique=True)  # idempotency key
    imap_uid: Mapped[int] = mapped_column(Integer, default=0)
    from_addr: Mapped[str] = mapped_column(String(255), default="")
    subject: Mapped[str] = mapped_column(String(500), default="")
    snippet: Mapped[str] = mapped_column(Text, default="")          # first ~500 chars of body
    label: Mapped[ReplyLabel | None] = mapped_column(
        Enum(ReplyLabel, values_callable=lambda e: [m.value for m in e]),
        nullable=True)                                              # set for replies, None for bounces
    matched_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
```

`CampaignState` gains two columns for the read-only incremental fetch:

```python
last_imap_uid: Mapped[int] = mapped_column(Integer, default=0)
imap_uidvalidity: Mapped[int] = mapped_column(Integer, default=0)
```

`init_db` runs `create_all`, so the new table and columns appear on next init
of a fresh DB — no migration tooling (local, re-creatable DB). Existing DBs are
re-initialised from scratch, consistent with prior phases.

### `app/config.py` — inbox settings (all `M2S_`-prefixed)

```python
imap_host: str = "imap.hostinger.com"
imap_port: int = 993
imap_user: str = ""            # defaults to smtp_user when blank
imap_password: str = ""        # defaults to smtp_password when blank
imap_mailbox: str = "INBOX"
no_response_days: int = 14
```

### `app/inbox/imap_client.py`

```python
from dataclasses import dataclass
from typing import Protocol

@dataclass
class FetchedMessage:
    uid: int
    imap_message_id: str          # inbound email's Message-ID header; falls back to
                                  # f"uid:{uid}" when the header is absent, so the
                                  # inbox_messages UNIQUE key is never an empty string
    from_addr: str
    subject: str
    in_reply_to: str              # In-Reply-To header ("" if absent)
    references: list[str]         # Message-IDs from References header
    body_text: str                # decoded text/plain body (first part)
    raw: str                      # full decoded source, for bounce Message-ID scraping
    received_at: datetime | None  # parsed Date header

class ImapClient(Protocol):
    def fetch_new(self, mailbox: str, since_uid: int,
                  uidvalidity: int) -> tuple[int, list[FetchedMessage]]:
        """Open `mailbox` read-only (no \\Seen). Read the server's UIDVALIDITY;
        if it differs from the caller's `uidvalidity`, ignore `since_uid` and
        return all messages (watermark reset), else return messages with
        UID > since_uid. Returns (server_uidvalidity, messages)."""
        ...

class HostingerImap:
    """Real IMAP4_SSL client. Not exercised in tests (they inject a fake)."""
    def __init__(self, host: str, port: int, user: str, password: str): ...
    def fetch_new(self, mailbox, since_uid, uidvalidity) -> tuple[int, list[FetchedMessage]]: ...
```

### `app/inbox/matching.py` — pure

```python
def match_reply(fetched, *, sent_by_message_id, contact_emails_by_startup):
    """Return (startup_id, message_id, matched_smtp_id) or None.
    Exact first: any id in fetched.in_reply_to/references that is a key of
    sent_by_message_id → that Message. Fallback: fetched.from_addr matches a
    contact email of a startup that has a SENT message → (startup_id, None,
    None)."""

def detect_bounce(fetched, *, sent_by_message_id) -> tuple[int, int] | None:
    """Return (startup_id, message_id) when fetched is a DSN/MAILER-DAEMON
    envelope (from_addr is mailer-daemon/postmaster, or the raw source is a
    delivery-status report) AND fetched.raw contains one of the stored
    smtp_message_ids. Else None."""
```

`sent_by_message_id` maps each stored `Message.smtp_message_id` → its
`(startup_id, message_id)`. Matching does no I/O; the service builds the maps
from the DB and passes them in.

### `app/inbox/classify.py` — injected Claude client

```python
def classify_reply(client, text: str) -> ReplyLabel:
    """One short Claude call returning interested / rejection / auto-reply /
    other. Contains (anthropic.AnthropicError, ValueError) and returns
    ReplyLabel.OTHER on failure or an unrecognised label — a reply is never
    lost to a classifier error."""
```

The real Anthropic client is built in the CLI (reusing `app/draft/`'s
construction); tests inject a fake returning a canned label.

### `app/inbox/service.py` — the orchestrator

```python
@dataclass
class InboxResult:
    replies: int
    bounces: int
    no_response: int
    fetched: int

def poll_inbox(session, *, imap, classifier, now, settings,
               limit: int | None = None, dry_run: bool = False) -> InboxResult:
    """One-shot poll:
      1. state = ensure_state(session); fetch (uidvalidity, msgs) =
         imap.fetch_new(settings.imap_mailbox, since_uid = state.last_imap_uid if
         uidvalidity matches else 0). Apply `limit` if given.
      2. Build sent_by_message_id + contact_emails_by_startup from the DB.
      3. Per fetched message, skip if inbox_messages already has its
         imap_message_id:
           • detect_bounce → Message.status = BOUNCED; demote the failed contact
             (verified=False); Startup.status = BOUNCED; write an
             InboxMessage(kind=BOUNCE, label=None) + Event 'bounce'.
           • else match_reply and the startup is not already REPLIED/BOUNCED →
             label = classify_reply(...); Message.status = REPLIED (when a
             Message was matched); Startup.status = REPLIED; write an
             InboxMessage(kind=REPLY, label) + Event 'reply'.
           • else ignore (not ours).
      4. Advance state.last_imap_uid to max uid seen; store uidvalidity.
      5. no_response sweep: each Startup in SENT whose newest SENT
         Message.sent_at < now - no_response_days and which has no reply/bounce
         → NO_RESPONSE + Event 'no_response'.
      6. dry_run: perform matching + classification and count, but make NO DB
         mutation — no InboxMessage, no status change, no watermark advance, no
         Event. Commit only on the real path.
    Returns InboxResult(replies, bounces, no_response, fetched)."""
```

## Data Flow

```
m2s inbox                         poll_inbox(now)
  └─ imap.fetch_new(INBOX, last_uid)  → new FetchedMessages (read-only, UID > watermark)
       └─ already recorded? ──yes─▶ skip (dedup on imap_message_id)
       └─ bounce?  ──yes─▶ Message→bounced, contact demoted, Startup→bounced,
       │                    InboxMessage(bounce) + Event 'bounce'
       └─ reply?   ──yes─▶ classify → label, Message→replied, Startup→replied,
                            InboxMessage(reply,label) + Event 'reply'
  └─ advance last_imap_uid / uidvalidity
  └─ no_response sweep: SENT & newest-SENT older than N days & no reply/bounce
                            → Startup→no_response + Event 'no_response'
```

Status pipeline advanced this phase:
`sent → replied` (reply) · `sent → bounced` (bounce) ·
`sent → no_response` (sweep). Existing statuses/enums cover all of these; only
`inbox_messages` and the two `CampaignState` columns are new.

## Error Handling

- **IMAP / network failure:** contained in `HostingerImap.fetch_new` with
  `(imaplib.IMAP4.error, OSError)`; the CLI reports a clear error and exits
  non-zero. A fetch failure records nothing and leaves the watermark untouched
  (safe to retry).
- **Claude classify failure or unknown label:** contained with
  `(anthropic.AnthropicError, ValueError)`; `classify_reply` returns
  `ReplyLabel.OTHER`. The reply is still recorded and the startup still moves to
  `replied` — a classifier outage never drops a reply.
- **Unmatched inbound mail:** not an error — ignored (not counted as reply or
  bounce).
- **Re-run / watermark reset:** the `imap_message_id` UNIQUE key and the
  `replied`/`bounced` status guards make reprocessing a no-op.
- **Malformed config (missing IMAP creds and no SMTP fallback):** the CLI
  reports a clear error and exits non-zero before constructing a client.
- No new bare excepts anywhere; nothing is auto-deleted; bounces demote (not
  delete) a contact.

## Testing

New files under `tests/`, all offline (injected `FakeImap` + fake classifier,
in-memory DB):

- `test_matching.py` — `match_reply`: exact `In-Reply-To`/`References` hit;
  `References`-only hit; `From`-address fallback; no match → None.
  `detect_bounce`: MAILER-DAEMON DSN carrying a known id → hit; ordinary mail
  from a stranger → None; DSN for an unknown id → None.
- `test_classify.py` — fake client returns a valid label → mapped to
  `ReplyLabel`; client raises `anthropic.AnthropicError` → `OTHER`; unknown
  string → `OTHER`.
- `test_inbox_service.py` — reply → `Startup` `replied`, `Message` `replied`,
  one `InboxMessage(reply,label)` row + `Event`; bounce → `Message` `bounced`,
  contact demoted (`verified=False`), `Startup` `bounced`, one
  `InboxMessage(bounce)` row + `Event`; `no_response` sweep moves an aged `sent`
  startup and leaves a fresh one; **idempotent** — a second `poll_inbox` over
  the same `FakeImap` writes no new rows and changes no status; **dry-run**
  mutates nothing (no row, no status, no watermark).
- `test_cli_inbox.py` — `m2s inbox` wires the real IMAP client + classifier
  construction, runs against an injected fake via the Typer runner + in-memory
  DB, reports `replies=/bounces=/no_response=`, and exits non-zero on missing
  credentials.

No existing test changes behavior. The `imaplib` import lives only in
`HostingerImap`; `anthropic` only in the CLI classifier construction — core
logic (matching, service, classify) takes the client/classifier as parameters.

## Documentation

- `.env.example`: add an "IMAP / inbox" block with the new `M2S_*` vars blank,
  plus a one-line note that they default to the SMTP credentials when blank.
- `README.md`: an "Inbox (Phase 5)" section — the `send → inbox` loop, the
  one-shot model with a sample Task Scheduler cadence (~10 min during the send
  window), what each status transition means, and `--dry-run`.

## Out of Scope (YAGNI)

- The day-5–7 follow-up send (next phase; depends on this detection).
- APScheduler daemon / any long-running poller.
- Full MIME thread reconstruction, HTML-body parsing, attachment handling.
- Multi-mailbox rotation, open-tracking pixels, A/B copy testing.
- Any re-target or re-send after a bounce: a bounced startup is terminal in this
  phase. Re-targeting another contact (relaxing the `draft_startup` guard,
  skipping bounced contacts in selection, re-draft → approve → send under human
  review) belongs to the re-send/follow-up phase.
