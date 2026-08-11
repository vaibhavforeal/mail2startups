# Phase 3 — AI Drafting — Design Spec

**Date:** 2026-08-11
**Status:** Approved by user (brainstorming session)
**Parent spec:** `2026-08-11-mail2startups-design.md` (the "AI drafting" section)

## Scope

Phase 3 builds **only the AI drafting subsystem** (`app/draft/`). Given an
`enriched` startup and its primary contact, it produces a per-startup tailored
email and — for formal mode — a tailored resume PDF, landing as a `drafts` row
in `pending_review`. Nothing is sent. Sending (`send/`) and reply tracking
(`inbox/`) are deferred to later phases, as is the web dashboard.

Because there is no dashboard yet, Phase 3 ships a **minimal CLI** to inspect
drafts. The "flip mode + regenerate in the review queue" interaction from the
parent spec is deferred with the dashboard; in Phase 3 the AI *suggests* a mode
and drafts accordingly.

## Requirements

- One Claude API call per startup returns structured JSON: suggested mode,
  chosen angle, selected project/experience ids, a rewritten 2-line summary,
  reordered skill emphasis, an email subject, and an email body matching the
  mode.
- **Hard guardrail:** the AI selects and re-phrases only. It cannot invent
  projects, numbers, or experience absent from `data/resume.yaml`. Any selected
  id not present in the resume is rejected; no draft is written.
- **Formal mode:** tailored email + a tailored resume PDF attached (path stored
  on the draft).
- **Casual mode:** conversational note, no attachment; `resume_pdf_path` is null.
- Idempotent: re-running only processes `enriched` startups that do not already
  have a draft. Crashes never double-draft.
- Fully offline-testable: every test mocks the Claude client; no live API calls.

## Decisions (this sub-project)

These specialize / override the parent spec for Phase 3:

| Topic | Parent spec | Phase 3 decision | Why |
|---|---|---|---|
| PDF renderer | Typst CLI | **ReportLab** (`pip install reportlab`) | Pure Python, stays in `.venv`, no manual binary / PATH setup on Windows, works offline, deterministic-enough for a text-extraction golden test. |
| `resume.yaml` population | "one-time setup converts existing resume" | **Hand-authored once** | Single resume; a PDF/DOCX→YAML converter is a whole extra feature to build and test for a one-time job. YAGNI. |
| Review surface | web review queue | **CLI inspection only** (`m2s drafts list` / `show`) | Dashboard is a later phase; drafting still needs to be inspectable now. |
| Drafting model | "Sonnet-class ≈ $3–8" | `settings.anthropic_model` (default `claude-opus-5`), overridable via `M2S_ANTHROPIC_MODEL` | Already configurable; user picks the cost/quality point per run. |

## Architecture

New package `app/draft/`, four focused modules, plus CLI wiring and a
hand-authored `data/resume.yaml`.

```
app/draft/
├── __init__.py
├── resume_schema.py   # Pydantic models for resume.yaml + validating loader
├── claude_draft.py    # the structured Claude call: (startup, contact, resume) -> DraftPlan
├── render.py          # ReportLab: (DraftPlan, resume) -> tailored PDF
└── service.py         # orchestration: draft_startup / draft_all, idempotency, Draft rows, events

data/resume.yaml       # user's structured experience (hand-authored)
out/resumes/           # generated per-startup PDFs
```

Existing pieces reused: `app/ai.py` pattern (`client.messages.parse` with
`output_format=<PydanticModel>` → `response.parsed_output`, injectable
`client`); `app/config.py` (`anthropic_model`, `ANTHROPIC_API_KEY` via `.env`);
`app/models.py` `Draft` / `DraftMode` / `DraftStatus`; `app/enrich/usage`-style
per-startup error containment from Phase 2 hunt.

## Data model

`data/resume.yaml` structure (validated by `resume_schema.py`):

```yaml
profile:
  name: str
  email: str
  phone: str
  location: str
  headline: str
links:
  github: str
  portfolio: str
  linkedin: str            # optional
education:
  - { school: str, degree: str, year: str, detail: str }
skills:
  - { name: str, tags: [web|ai|data] }
projects:
  - id: str                # stable handle the Claude call selects by; unique
    name: str
    tags: [web|ai|data]
    summary: str
    impact: [str, ...]     # bullet lines
    link: str              # optional
experience:
  - id: str                # unique across projects+experience
    org: str
    role: str
    dates: str
    tags: [web|ai|data]
    impact: [str, ...]
```

Loader rules: required fields present; `id`s unique across `projects` +
`experience`; every tag in the allowed set. A malformed file raises a clear
`ValueError` naming the problem — fail fast, before any Claude call.

### `DraftPlan` — the Claude structured return

```python
class DraftPlan(BaseModel):
    mode: Literal["formal", "casual"]
    angle: Literal["swe", "ai", "data"]
    experience_ids: list[str]   # selected experience entries; must exist in resume.yaml
    project_ids: list[str]      # selected projects; must exist in resume.yaml
    summary: str                # rewritten 2-line profile summary
    skill_order: list[str]      # reordered skill names for emphasis
    subject: str
    body: str                   # matches mode; references something concrete
```

Validation after the call: every `experience_ids` and `project_ids` entry must
resolve against the loaded resume (ids are unique across the two sections). An
unresolved id is a guardrail violation → no draft written, an
`Event(kind="draft_failed", payload={"reason": "invalid_id"})` is logged.
`experience_ids` may be empty (e.g. a candidate with only projects).

## Data flow

```
enriched startup + primary contact + resume.yaml
        │
        ▼  claude_draft.draft_plan(...)  (mocked in tests)
   DraftPlan (validated against resume — guardrail)
        │
        ├── formal → render.render_resume(plan, resume) → out/resumes/<Name>_Resume_<Startup>.pdf
        └── casual → no PDF
        │
        ▼
   Draft row (status=pending_review, mode, subject, body, resume_pdf_path)
   + Event(kind="drafted")
```

`draft_all(session, limit=...)` selects startups in status `enriched` that have
no existing `Draft`, drafts each, and contains any per-startup failure (Claude
API error, malformed JSON after one retry, guardrail violation) as an `Event`
without aborting the batch — mirroring Phase 2 `hunt_all`.

The draft links to the startup's best contact, chosen by the Phase 2 ranking
order (scraped founder > api-found > verified pattern guess > generic inbox).
The implementation plan verifies the exact `Contact` field used to express that
rank against the current model.

## Rendering (ReportLab)

Single-column A4 template, one function `render_resume(plan, resume) -> Path`:

1. Header — `profile.name`, `headline`, contact line (email · phone ·
   location), links (github · portfolio).
2. Summary — the tailored 2-line `plan.summary`.
3. Experience — the entries named in `plan.experience_ids`, in that order, each
   with org, role, dates, and impact bullets. Section omitted if the list is
   empty.
4. Projects — the entries named in `plan.project_ids`, in that order, each with
   name, one-line summary, and impact bullets.
5. Skills — names in `plan.skill_order` (unlisted skills appended).
6. Education — as listed.

Output path: `out/resumes/<profile.name>_Resume_<startup.name>.pdf` (filename
sanitized). Casual mode does not call this.

## CLI

Added to `app/cli.py`:

1. `m2s draft [--limit N] [--startup DOMAIN] [--dry-run]`
   - Drafts eligible enriched startups (or one `--startup`). `--dry-run` prints
     the resolved `DraftPlan` and would-be PDF path without writing rows or PDFs.
2. `m2s drafts list`
   - Table of `pending_review` drafts: startup, mode, subject, PDF? (y/n).
3. `m2s drafts show <draft_id>`
   - Prints subject, body, mode, and the resolved resume PDF path.

## Error handling

- Malformed Claude JSON (parse returns `None` / invalid) → one retry with error
  feedback appended to the prompt; still bad → skip that startup, log
  `Event(kind="draft_failed", payload={"reason": "malformed_response"})`.
- Claude API / transport error → contained per-startup (log `draft_failed`,
  `reason="provider_error"`), batch continues. Same containment discipline as
  Phase 2 `hunt` (`except (anthropic error types, ValueError)`, not bare
  `except Exception`, so internal logic bugs still surface).
- Guardrail violation (invalid id) → skip, log `reason="invalid_id"`.
- Missing/malformed `resume.yaml` → hard fail at load, before any drafting.
- Nothing is auto-deleted; failures leave the startup in `enriched` for a later
  re-run.

## Testing

All tests fully offline; the Claude client is always injected/mocked. New test
files under `tests/`:

- **`test_resume_schema.py`** — valid yaml loads; missing required field,
  duplicate id, and unknown tag each raise with a clear message.
- **`test_claude_draft.py`** — with a mocked client returning a canned
  `DraftPlan`, the call maps fields correctly; a plan referencing an id absent
  from the resume is rejected (guardrail).
- **`test_render.py`** — golden-ish: render a fixture plan, extract text from
  the PDF, assert the selected projects + tailored summary appear and an
  unselected project does not; assert the file lands under `out/resumes/`.
- **`test_draft_service.py`** — `draft_all` drafts only `enriched`
  startups without an existing draft (idempotency); formal → `resume_pdf_path`
  set, casual → null; a mocked Claude failure on one startup is contained
  (`draft_failed` event) while the rest of the batch completes.

## Out of scope (Phase 3)

- Sending / SMTP / drip scheduling / follow-ups (`send/`).
- IMAP polling, bounce/reply parsing, reply classification (`inbox/`).
- Web dashboard and the interactive review queue (mode flip + regenerate).
- A `resume.yaml` importer/converter CLI.
- Multiple resumes or per-role resume variants beyond angle selection within one
  `resume.yaml`.
