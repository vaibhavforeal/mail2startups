# Mail2Startups

Automated internship outreach: discover startups, hunt contact emails,
AI-tailor resumes and emails, drip-send via Hostinger, track replies.

**Status: Phase 2 (email hunting & enrichment) complete.**
Spec: `docs/superpowers/specs/2026-08-11-mail2startups-design.md`

## Setup

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
cp .env.example .env   # then fill in ANTHROPIC_API_KEY etc.
```

## Drafting backend (Anthropic or Azure Foundry)

`m2s draft` picks a Claude backend from whichever credentials are set:

- **`ANTHROPIC_API_KEY` present** → the direct Anthropic API (default).
- **Absent, Foundry configured** → a Claude deployment in Azure AI Foundry
  (`M2S_AZURE_FOUNDRY_API_KEY` + `M2S_AZURE_FOUNDRY_RESOURCE`, deployment name
  in `M2S_AZURE_FOUNDRY_MODEL`; set `M2S_AZURE_FOUNDRY_BASE_URL` instead of
  `M2S_AZURE_FOUNDRY_RESOURCE` for a non-standard endpoint — `RESOURCE` takes
  precedence when both are set).
- **Neither** → `m2s draft` reports a configuration error.

The Foundry deployment must be **"Hosted on Anthropic"** — the "Hosted on
Azure" option rejects the structured-output request drafting relies on.

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

## Inbox (Phase 5)

Close the read side of the loop: poll the mailbox, record replies and bounces,
and age out silent sends.

```bash
m2s inbox --dry-run          # match + classify inbound mail, change nothing
m2s inbox                    # record replies/bounces; sweep stale sends to no_response
m2s inbox --limit 50         # cap messages processed this run
```

`m2s inbox` fetches new mail read-only by IMAP UID (it never marks messages
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

## Tests

```bash
.venv/Scripts/python -m pytest
```
