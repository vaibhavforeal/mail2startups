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
