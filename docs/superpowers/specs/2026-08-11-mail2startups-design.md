# Mail2Startups — Design Spec

**Date:** 2026-08-11
**Status:** Approved by user (brainstorming session)

## Purpose

Automate internship outreach to a few hundred startups: discover startups from
directories, hunt founder/CTO contact emails, generate a per-startup tailored
resume PDF and email with the Claude API, drip-send through the user's Hostinger
mailbox, and track sent / bounced / replied — all controlled from a local web
dashboard with a human review queue before anything is sent.

## Requirements (from brainstorming)

- **Roles:** multiple tech roles (SWE / AI / data) — resume angle chosen per startup.
- **Geography:** India-based startups prioritized, plus global remote-friendly ones.
- **Email targets:** founders/CTO first, generic inboxes (careers@, hello@) as fallback.
- **Budget:** free tiers of Hunter/Apollo/Snov only; own scraping system is a
  first-class feature the user wants to build.
- **AI:** Claude API for all drafting/classification.
- **Resume:** built from scratch as structured data (`resume.yaml`), AI selects and
  phrases content per startup, rendered to PDF.
- **Pacing:** drip ~25–40/day, week-1 ramp at 10–15/day.
- **Control:** local web dashboard; every email (including follow-ups) passes a
  human review queue before sending.
- **Follow-ups:** exactly one, at day 5–7 if no reply and no bounce.

## Architecture

Single Python monolith. FastAPI serves an HTMX dashboard; APScheduler runs in the
same process for drip-sending and IMAP polling. SQLite is the single source of
truth. Every pipeline stage is also exposed as a CLI command (`m2s scrape`,
`m2s enrich`, `m2s draft`, `m2s status`, ...) for manual/partial runs.

```
mail2startups/
├── app/
│   ├── main.py            # FastAPI + scheduler startup
│   ├── models.py          # SQLite schema (SQLAlchemy)
│   ├── cli.py             # m2s command-line entry points
│   ├── scraper/
│   │   ├── sources/       # directory adapters (see below)
│   │   ├── site_crawler.py# per-domain page fetcher (httpx+BS4, Firecrawl fallback)
│   │   └── email_finder.py# extraction, founder discovery, pattern guessing
│   ├── enrich/            # Hunter/Apollo/Snov free-tier clients, verification
│   ├── draft/             # Claude client, prompts, Typst rendering
│   ├── send/              # SMTP sender, drip scheduler, follow-up logic
│   ├── inbox/             # IMAP poller, bounce parser, reply classifier
│   └── web/               # dashboard routes + HTMX templates
├── data/
│   ├── resume.yaml        # user's experience/projects as tagged structured data
│   ├── resume_template.typ
│   └── m2s.db
└── out/resumes/           # generated per-startup PDFs
```

Key stack choices: Python 3.12+, FastAPI, SQLAlchemy + SQLite, APScheduler,
httpx + BeautifulSoup, Typst CLI for PDF rendering, HTMX (no JS build step),
smtplib/imaplib, Anthropic Python SDK.

## Data model

Startup status pipeline:
`discovered → enriched → drafted → in_review → queued → sent → replied | bounced | no_response | dead`

| Table | Purpose | Key fields |
|---|---|---|
| `startups` | one row per company | name, domain, source, location, industry, description, status |
| `contacts` | people/inboxes per startup | startup_id, name, role, email, found_via (scraped / api / pattern_guess / generic), confidence, verified |
| `drafts` | AI output awaiting review | startup_id, contact_id, subject, body, resume_pdf_path, status (pending_review / approved / rejected), user_edits |
| `messages` | every actual send | draft_id, type (initial / followup), sent_at, smtp_message_id, status (queued / sent / failed / bounced / replied) |
| `events` | append-only log | startup_id, kind (bounce / reply / error / retry / pause), payload, timestamp |

Reply matching is exact: sent `Message-ID`s are stored and matched against
`In-Reply-To` / `References` headers of incoming mail.

All stages are idempotent — re-running a stage only processes rows in the
eligible status, so crashes never double-process or double-send.

## Startup discovery: directory adapters

`scraper/sources/` defines an adapter interface (`fetch() -> list[StartupRecord]`).
Adapters capture name, domain, description, location, and any contact/social data
the directory already exposes. V1 adapters:

| Adapter | Coverage | Method |
|---|---|---|
| Y Combinator directory | global + remote, filterable by hiring status | public Algolia search API (JSON) |
| Product Hunt | fresh global startups | official GraphQL API (free token) |
| Startup India portal | DPIIT-recognized Indian startups by sector/state | public directory scrape |
| Listicle importer | Inc42/YourStory/"top N startups" articles | user pastes URL; Claude extracts names + domains |
| CSV import | anything else (friend lists, trial exports) | manual file import |

Deliberately excluded: Wellfound and LinkedIn scraping (aggressive anti-bot,
account-ban risk). The listicle/CSV routes cover those companies indirectly.

## Email hunting & enrichment

Per startup domain, cheapest method first:

1. **Site crawl:** fetch `/`, `/about`, `/team`, `/contact`, `/careers` with
   httpx + BeautifulSoup; Firecrawl CLI only as fallback for JS-heavy sites
   (credits are finite and tracked).
2. **Extract:** `mailto:` links, plaintext emails including obfuscations
   (`name [at] domain`), founder/team names + roles from team pages.
3. **Founder name gap-fill:** exa web search ("<startup> founder CTO") — names
   only; no LinkedIn scraping.
4. **Pattern guessing:** generate candidates from name + domain
   (`first@`, `first.last@`, `flast@`, ...), verify via MX lookup + free
   verifier APIs; store a confidence score per candidate.
5. **Free-tier enrichment:** Hunter (~25/mo), Apollo, Snov credits spent only
   where scraping + guessing failed; remaining credits tracked in the DB.
6. **Ranking:** scraped founder email > API-found > verified pattern guess >
   generic inbox. All contacts stored; best becomes primary target.

## AI drafting

- `data/resume.yaml` holds profile, education, skills, projects, and experience,
  each item tagged by domain (web / ai / data / ...) with impact bullets.
  One-time setup converts the user's existing resume into this format.
- One Claude API call per startup returns structured JSON: chosen angle
  (SWE / AI / data), selected project IDs, rewritten 2-line summary, reordered
  skill emphasis, email subject, and email body (< ~150 words, references
  something concrete about the startup, plain tone, no flattery).
- **Hard guardrail:** the AI selects and re-phrases only — it cannot invent
  projects, numbers, or experience absent from `resume.yaml`.
- Rendering is mechanical: selected content flows into the Typst template and
  compiles to `out/resumes/<Name>_Resume_<Startup>.pdf`.
- Estimated cost: a few hundred startups × 1 call ≈ $3–8 (Sonnet-class model).
- Output lands in `drafts` as `pending_review`; nothing sends without approval.

## Sending & scheduling

- SMTP via `smtp.hostinger.com:465` (SSL); credentials in `.env`.
- Send window default 9:30–18:30 IST weekdays; randomized 15–30 min gaps;
  daily cap 30 (config), week-1 ramp 10–15/day. Only `approved` drafts send.
- If the machine is off, the scheduler catches up at next start without ever
  exceeding the daily cap.
- Emails are plain text with the tailored PDF attached.
- **Follow-up:** day 5–7 after initial send with no reply/bounce, a short nudge
  is auto-drafted into the same thread (`In-Reply-To`) and enters the review
  queue like any draft. Exactly one follow-up per startup.

## Tracking

- IMAP poller (imap.hostinger.com) runs every ~10 min during the send window.
- Replies matched via `In-Reply-To`/`References` ↔ stored Message-IDs.
- Bounces parsed from MAILER-DAEMON messages; a bounced pattern-guessed address
  automatically re-queues the draft to the next-best contact for that startup.
- Each reply classified by a quick Claude call:
  interested / rejection / auto-reply / other.

## Dashboard (HTMX)

- **Overview:** funnel stats (discovered → … → replied), today's send queue,
  enrichment credits remaining, domain-health warnings, global pause button.
- **Startups:** filterable table; per-company detail with event timeline.
- **Review queue:** email text + rendered resume PDF side by side; inline edit;
  approve / reject; bulk approve.
- **Replies:** classified list, highlighting "interested".

## Deliverability & safety rails

- Pre-flight script verifies SPF/DKIM/DMARC DNS records before the first send.
- First-run protocol: 5 test sends to the user's own Gmail/Outlook accounts to
  confirm inbox placement before the campaign starts.
- 3 consecutive SMTP failures → auto-pause + dashboard alert.
- Manual pause halts the scheduler immediately.
- Scraper is polite: respects robots.txt, per-domain timeouts, 2–3 retries,
  then marks `scrape_failed` for manual attention.

## Error handling

- Statuses gate every stage; all failures append to `events` with retry counts.
- Claude API: exponential backoff; malformed JSON → one retry with error
  feedback, then flag for manual review.
- Nothing is auto-deleted; dead ends get status `dead`, preserving history.

## Testing

- **Unit:** email regex + de-obfuscation, pattern generator, bounce parser,
  contact ranking, Typst render (golden file).
- **Integration:** full pipeline against saved HTML fixtures — no live scraping
  in tests.
- **Dry-run:** `--dry-run` routes real SMTP sends to the user's own address.

## Out of scope (v1)

- Wellfound/LinkedIn scraping, open-tracking pixels, multi-mailbox rotation,
  A/B testing of email copy, more than one follow-up, deployment anywhere
  other than the user's own machine.
