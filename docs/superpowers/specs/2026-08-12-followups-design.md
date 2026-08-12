# Follow-ups — Design Spec

**Date:** 2026-08-12
**Phase:** 6 (Follow-ups)
**Status:** Approved for planning

## Goal

Send a single, Claude-drafted, threaded follow-up to a startup that received an
initial email and has stayed silent for a set number of days, reusing the
existing review-and-send pipeline end-to-end. Detection of replies/bounces
(Phase 5) already runs; this phase adds the one nudge that happens *before* a
silent startup is given up as `no_response`.

## Policy (fixed decisions)

- **Count:** exactly one follow-up per startup, ever.
- **Timing:** eligible once the initial send is `followup_delay_days` old
  (default **5**), only if no reply and no bounce have been recorded.
- **Content:** Claude generates a short (2–3 line) nudge, grounded **only** in
  the candidate resume and the original email — same no-invention rule as the
  initial draft. No new facts, numbers, or claims.
- **Threading:** the follow-up is a reply to the original — it sets
  `In-Reply-To` and `References` to the initial send's stored `Message-ID`, and
  its subject is `Re: <original subject>`. No resume PDF re-attached.
- **Review gate:** the follow-up goes through the **same** human approve/reject
  gate as initial drafts. Nothing AI-written is sent unreviewed.
- **Give-up clock:** a startup is swept to `no_response` **14 days after its
  most recent outbound message** (`no_response_days`, default 14). With no
  follow-up sent that is day 14 from the initial; with a follow-up sent on day 5
  that is day 19. This is the "14 days of silence since last contact" rule.

### Timeline

```
initial send      → day 0
followup eligible → day 5   (followup_delay_days; only if still silent)
followup sent     → day 5+  (after human approval)
no_response sweep → 14 days after the most recent outbound
                    (day 14 if no follow-up; day 19 if followed up on day 5)
```

## Approach

**Chosen: explicit `Draft.type` marker, reuse the whole send pipeline.**

A follow-up is an ordinary `Draft` row tagged `type=FOLLOWUP`. It is generated
into `pending_review`, approved/rejected with the existing `approve`/`reject`
commands, and shipped by the existing `send` command. The send path is lightly
generalized to (a) select any not-yet-sent approved draft rather than only
initial drafts, and (b) set threading headers + `Message.type=FOLLOWUP` when the
draft is a follow-up.

Rejected alternatives:

- **Separate follow-up subsystem** (own table + parallel generate/approve/send):
  duplicates pacing/window/cap/review machinery for a single follow-up.
  Violates DRY/YAGNI.
- **Implicit derivation** (no column; infer "is follow-up" from the startup
  already having an INITIAL message): generation-idempotency and send-path
  branching become fragile history guesses; can't distinguish a pending from a
  rejected follow-up. Brittle.

## Components

### 1. Data model (`app/models.py`)

- Add `Draft.type: Mapped[MessageType] = mapped_column(default=MessageType.INITIAL, nullable=False)`
  — reuse the existing `MessageType` enum (`INITIAL` / `FOLLOWUP`). This mirrors
  the way Phase 5 added columns (e.g. `CampaignState.last_imap_uid`): the schema
  is created from the models via `init_db`/`create_all`, so a fresh DB gets the
  column and every initial draft defaults to `INITIAL`.

No new tables. `Message.type` (INITIAL/FOLLOWUP) and `Message.smtp_message_id`
already exist and carry the threading anchor.

### 2. Follow-up generation (`app/followup/`)

New package mirroring `app/draft/`.

**`app/followup/claude_followup.py`**

```python
class FollowupPlan(BaseModel):
    body: str

def followup_plan(startup, contact, resume, original_subject, original_body,
                  *, client=None, model=None) -> FollowupPlan:
    ...
```

- Prompt: "Write a short (2–3 sentence) follow-up nudge to a startup that has
  not replied to the email below. Reference nothing that is not already in the
  original email or the resume. No new projects, numbers, or claims. Plain
  tone, no pressure." Includes the original subject + body and the resume JSON
  as grounding.
- Uses the same `client.messages.parse(..., output_format=FollowupPlan)` shape
  as `draft_plan`, with the same one-retry-on-malformed loop and
  `MalformedFollowupError` (or reuse `MalformedDraftError`).
- Backend selection reuses `resolve_backend()` from `app.draft.claude_draft`.
- `client` is injectable → fully offline tests with a fake generator.

**`app/followup/service.py`**

```python
@dataclass
class FollowupResult:
    startup_id: int
    drafted: bool

def draft_followups(session, *, resume, now, settings,
                    generator=followup_plan, limit=50) -> list[FollowupResult]:
    ...
```

Eligibility for a follow-up draft (all must hold):

- `startup.status == SENT` (replied/bounced/no_response/dead are excluded by
  status; a startup mid-follow-up sits in QUEUED and is excluded too).
- The startup's INITIAL `Message` was sent `>= followup_delay_days` before
  `now` (compare `_as_utc(sent_at)` against `now - timedelta(days=...)`, reusing
  the Phase-5 tz-naive handling).
- No existing FOLLOWUP `Draft` for the startup (any status) **and** no FOLLOWUP
  `Message` — guarantees exactly one follow-up ever, and makes the command
  idempotent across runs.

For each eligible startup, look up the initial draft's `subject`/`body` for
grounding + the `Re:` subject, call the generator, and add:

```python
Draft(startup_id=..., contact_id=<same as initial>, type=MessageType.FOLLOWUP,
      mode=DraftMode.CASUAL, subject="Re: " + original_subject,
      body=plan.body, resume_pdf_path=None, status=DraftStatus.PENDING_REVIEW)
```

Log a `followup_drafted` Event. Per-startup failure containment mirrors
`draft_one` (rollback + `followup_failed` Event on provider/parse error; skip,
don't abort the batch). Startup status is **not** changed at generation time —
it stays `SENT` until `approve` moves it to `QUEUED`.

### 3. Threading in the send path (`app/send/`)

**`app/send/smtp_client.py` — `build_email`** gains two optional params:

```python
def build_email(*, from_email, from_name, to, subject, body, pdf_path,
                in_reply_to: str | None = None,
                references: str | None = None) -> EmailMessage:
    ...
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
```

Initial sends pass neither and behave exactly as today.

**`app/send/service.py` — `_send_one`**: when `draft.type == FOLLOWUP`, fetch the
startup's INITIAL `Message.smtp_message_id`:

```python
initial_smtp = session.scalar(
    select(Message.smtp_message_id)
    .join(Draft, Message.draft_id == Draft.id)
    .where(Draft.startup_id == sid, Message.type == MessageType.INITIAL,
           Message.smtp_message_id.is_not(None))
    .order_by(Message.id).limit(1))
```

Pass it as both `in_reply_to` and `references`, and record
`Message(type=MessageType.FOLLOWUP, ...)`. On success the startup returns to
`SENT` (same terminal as an initial send). If `initial_smtp` is unexpectedly
`None`, send un-threaded rather than failing (defensive; should not happen for a
correctly generated follow-up).

### 4. Review + send reuse (state machine)

No changes to `approve_drafts` / `reject_drafts`. The only send-service change
besides threading:

**`_eligible_drafts`** currently excludes drafts whose id appears in an INITIAL
`Message`. Generalize to exclude drafts that already have **any** `Message`:

```python
Draft.id.not_in(select(Message.draft_id))
```

so a sent initial is never re-selected and an approved-but-unsent follow-up is.

State flow for a follow-up:

```
SENT ──(followups: create FOLLOWUP draft, pending_review)──► SENT
SENT ──(approve <id>)──► QUEUED
QUEUED ──(send: thread + Message.type=FOLLOWUP)──► SENT
```

### 5. `no_response` sweep reconciliation (`app/inbox/service.py`)

`_sweep_no_response` already measures from `_newest_sent_at` (max SENT
`sent_at`), so the give-up clock is automatically "14 days after the most recent
outbound" — no change needed for the day-19-if-followed-up behavior.

Add one guard so a startup is not swept while a follow-up is **awaiting review
or sending**: skip any `SENT` startup that has a FOLLOWUP `Draft` in
`PENDING_REVIEW` or `APPROVED` with no corresponding `Message` yet. (An approved
follow-up already moves the startup to `QUEUED`, off the `SENT` filter; this
guard covers the `PENDING_REVIEW` window where the startup is still `SENT`.) A
**rejected** follow-up does not block the sweep — that startup is genuinely
done and should be given up on schedule.

### 6. Config, CLI, docs

**`app/config.py`**: add `followup_delay_days: int = 5`.

**`app/cli.py`**: add a flat command

```
m2s followups [--limit N] [--dry-run]
```

- Builds the injected generator (closure over `resolve_backend`, mirroring the
  Phase-5 `_build_classifier` pattern), loads the resume, calls
  `draft_followups(...)`, prints `followups: drafted=<n>`.
- `--dry-run` computes eligibility and prints counts without persisting
  (rollback), mirroring other dry-run commands.
- Reuses `approve` / `reject` / `send` — no new review or send command.

**`.env.example`**: add `M2S_FOLLOWUP_DELAY_DAYS=5` (with the existing blanks
convention).

**`README.md`**: add a "Follow-ups (Phase 6)" section — the one-follow-up
policy, the `followups → approve → send` flow, and the give-up-clock semantics.

## Testing (all offline)

Injected generator + injected transport; no network, no real Claude/SMTP.

- **Generation eligibility**: silent-for-5-days SENT startup gets a follow-up
  draft; too-recent send does not; replied/bounced/no_response/dead do not.
- **Idempotency**: a second `draft_followups` run creates no second follow-up
  (existing FOLLOWUP draft blocks it); a startup with a FOLLOWUP message is
  skipped.
- **Grounding**: generator receives the original subject/body + resume; the
  created draft is `type=FOLLOWUP`, `mode=CASUAL`, subject `Re: ...`,
  `resume_pdf_path=None`, `pending_review`.
- **Threading**: sending a FOLLOWUP draft sets `In-Reply-To` and `References`
  to the initial send's Message-ID; records `Message(type=FOLLOWUP)`; startup
  → `SENT`. Initial sends still set neither header.
- **Eligibility generalization**: a sent initial draft is not re-selected;
  an approved follow-up draft is selected.
- **Sweep reconciliation**: a startup followed up on day 5 is swept on day 19,
  not day 14; a startup with a pending-review follow-up is not swept at day 14;
  a startup with a rejected follow-up is still swept on schedule.
- **CLI**: `m2s followups` drafts and prints `drafted=<n>`; `--dry-run`
  persists nothing; missing backend config surfaces a clear error/exit.

## Out of scope

- More than one follow-up, or configurable follow-up count/cadence beyond the
  single delay knob.
- Auto-send (no review) or guardrail-diverted auto-send.
- Reply-aware follow-up content (the classifier's label does not influence the
  nudge; replied startups simply get no follow-up).
- Any change to discovery, enrichment, initial drafting, or inbox detection
  beyond the two named touch-points (`_eligible_drafts` generalization and the
  `_sweep_no_response` guard).
