# Phase 3 — AI Drafting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn each `enriched` startup into a reviewable `Draft` (tailored email + a ReportLab resume PDF for formal mode) via one structured Claude call, guarded so the AI can only select/rephrase real `resume.yaml` content.

**Architecture:** New `app/draft/` package with four focused modules — `resume_schema` (validated `resume.yaml` loader), `claude_draft` (the structured Claude call → `DraftPlan` + id guardrail), `render` (ReportLab PDF), `service` (idempotent orchestration writing `Draft` rows) — plus CLI commands. Mirrors Phase 2's injected-dependency + per-item error-containment style.

**Tech Stack:** Python 3.12+, Pydantic v2, PyYAML, ReportLab, Anthropic SDK (`messages.parse` structured output, as in `app/ai.py`), SQLAlchemy 2.x, Typer, pytest (offline; Claude client always injected), pypdf (test-only, for PDF text extraction).

## Global Constraints

- **Fully offline tests.** Every test injects/mocks the Claude client and renderer where relevant. No live API or network calls in the suite. Run tests with `.venv/Scripts/python -m pytest`.
- **Error containment mirrors Phase 2 `hunt_all`.** Batch loops catch `(anthropic.AnthropicError, ValueError)` — never bare `except Exception` (internal logic bugs must still surface). Per-startup faults log an `Event(kind="draft_failed", ...)` and never abort the batch.
- **Hard guardrail:** the AI selects/rephrases only. Every `experience_ids`/`project_ids` value must resolve against the loaded `resume.yaml`; any unknown id → no draft written, `draft_failed` event with `reason="invalid_id"`.
- **Idempotent:** only `StartupStatus.ENRICHED` startups **without** an existing `Draft` are processed; a successful draft sets status to `DRAFTED`. Re-runs never double-draft.
- **Anthropic model** comes from `settings.anthropic_model` (default `claude-opus-5`), overridable via `M2S_ANTHROPIC_MODEL`. Never hardcode a model string.
- **Personal data:** `data/resume.yaml` is gitignored; commit `data/resume.example.yaml` as the template.
- **Commit trailer:** end each commit message with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## File Structure

- Create: `app/draft/__init__.py` (empty package marker)
- Create: `app/draft/resume_schema.py` — Pydantic `Resume` models + `load_resume`
- Create: `app/draft/claude_draft.py` — `DraftPlan`, `MalformedDraftError`, `unknown_ids`, `build_prompt`, `draft_plan`
- Create: `app/draft/render.py` — `render_resume`
- Create: `app/draft/service.py` — `DraftResult`, `select_primary_contact`, `draft_startup`, `draft_all`
- Create: `data/resume.example.yaml` — template (committed)
- Create: `tests/fixtures/resume_min.yaml` — test fixture
- Create: `tests/test_resume_schema.py`, `tests/test_claude_draft.py`, `tests/test_render.py`, `tests/test_draft_service.py`, `tests/test_cli_draft.py`
- Modify: `pyproject.toml` (add deps), `app/config.py` (add `resume_path`), `app/cli.py` (add `draft` + `drafts` commands), `.gitignore` (ignore `data/resume.yaml`)

---

## Task 1: Resume schema, loader, deps, config

**Files:**
- Modify: `pyproject.toml`
- Modify: `app/config.py`
- Modify: `.gitignore`
- Create: `app/draft/__init__.py`
- Create: `app/draft/resume_schema.py`
- Create: `data/resume.example.yaml`
- Create: `tests/fixtures/resume_min.yaml`
- Test: `tests/test_resume_schema.py`

**Interfaces:**
- Produces: `Resume` (Pydantic model with `.profile`, `.links`, `.education`, `.skills`, `.projects`, `.experience`); `Profile`/`Links`/`Education`/`Skill`/`Project`/`Experience` submodels; `ALLOWED_TAGS: set[str]`; `load_resume(path: str | Path) -> Resume` (raises `ValueError` on any problem); `Settings.resume_path: Path`.

- [ ] **Step 1: Add dependencies and install**

Edit `pyproject.toml` — add to `dependencies`: `"pyyaml>=6.0"`, `"reportlab>=4.0"`. Add to `[project.optional-dependencies] dev`: `"pypdf>=4.0"`. Then run:

```bash
.venv/Scripts/python -m pip install -e ".[dev]"
```

Expected: reportlab, pyyaml, pypdf install successfully.

- [ ] **Step 2: Add `resume_path` to settings**

In `app/config.py`, add inside `Settings` (after `db_path`):

```python
    resume_path: Path = Path("data/resume.yaml")
```

- [ ] **Step 3: Ignore personal resume data**

Append to `.gitignore`:

```
data/resume.yaml
```

- [ ] **Step 4: Write the test fixture**

Create `tests/fixtures/resume_min.yaml`:

```yaml
profile:
  name: Vaibhav Shettar
  email: vaibhav@example.com
  phone: "+91-90000-00000"
  location: Bengaluru, India
  headline: Software Engineer — AI & Backend
links:
  github: https://github.com/vaibhav
  portfolio: https://vaibhav.dev
education:
  - school: Example Institute of Technology
    degree: B.Tech, Computer Science
    year: "2025"
    detail: GPA 8.9/10
skills:
  - { name: Python, tags: [web, ai, data] }
  - { name: FastAPI, tags: [web] }
  - { name: PyTorch, tags: [ai] }
projects:
  - id: p_ai
    name: RagPipeline
    tags: [ai]
    summary: Retrieval-augmented QA over private docs.
    impact:
      - Cut hallucinations 40% with reranking.
      - Served 200 QPS at p95 300ms.
    link: https://github.com/vaibhav/ragpipeline
  - id: p_web
    name: ShopFront
    tags: [web]
    summary: Headless e-commerce storefront.
    impact:
      - Lighthouse 98 across pages.
experience:
  - id: e_intern
    org: Acme Labs
    role: SWE Intern
    dates: Summer 2024
    tags: [web, ai]
    impact:
      - Built the internal search service in FastAPI.
```

- [ ] **Step 5: Write the failing tests**

Create `tests/test_resume_schema.py`:

```python
from pathlib import Path

import pytest

from app.draft.resume_schema import load_resume

FIXTURE = Path(__file__).parent / "fixtures" / "resume_min.yaml"


def test_load_valid_resume():
    resume = load_resume(FIXTURE)
    assert resume.profile.name == "Vaibhav Shettar"
    assert {p.id for p in resume.projects} == {"p_ai", "p_web"}
    assert resume.experience[0].id == "e_intern"


def test_missing_file_raises(tmp_path):
    with pytest.raises(ValueError, match="not found"):
        load_resume(tmp_path / "nope.yaml")


def test_missing_required_field_raises(tmp_path):
    bad = tmp_path / "r.yaml"
    bad.write_text("links: {github: x}\n", encoding="utf-8")  # no profile
    with pytest.raises(ValueError):
        load_resume(bad)


def test_duplicate_id_raises(tmp_path):
    bad = tmp_path / "r.yaml"
    bad.write_text(
        "profile: {name: A, email: a@b.c}\n"
        "projects:\n  - {id: dup, name: X}\n"
        "experience:\n  - {id: dup, org: Y, role: Z}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_resume(bad)


def test_unknown_tag_raises(tmp_path):
    bad = tmp_path / "r.yaml"
    bad.write_text(
        "profile: {name: A, email: a@b.c}\n"
        "skills:\n  - {name: Python, tags: [quantum]}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown tag"):
        load_resume(bad)
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_resume_schema.py -v`
Expected: FAIL (`app.draft.resume_schema` does not exist).

- [ ] **Step 7: Implement the schema and loader**

Create `app/draft/__init__.py` (empty). Create `app/draft/resume_schema.py`:

```python
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError, model_validator

ALLOWED_TAGS: set[str] = {"web", "ai", "data"}


class Profile(BaseModel):
    name: str
    email: str
    phone: str = ""
    location: str = ""
    headline: str = ""


class Links(BaseModel):
    github: str = ""
    portfolio: str = ""
    linkedin: str = ""


class Education(BaseModel):
    school: str
    degree: str = ""
    year: str = ""
    detail: str = ""


class Skill(BaseModel):
    name: str
    tags: list[str] = []


class Project(BaseModel):
    id: str
    name: str
    tags: list[str] = []
    summary: str = ""
    impact: list[str] = []
    link: str = ""


class Experience(BaseModel):
    id: str
    org: str
    role: str = ""
    dates: str = ""
    tags: list[str] = []
    impact: list[str] = []


class Resume(BaseModel):
    profile: Profile
    links: Links = Links()
    education: list[Education] = []
    skills: list[Skill] = []
    projects: list[Project] = []
    experience: list[Experience] = []

    @model_validator(mode="after")
    def _check_ids_and_tags(self) -> "Resume":
        ids = [p.id for p in self.projects] + [e.id for e in self.experience]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate resume ids: {dupes}")
        for item in (*self.skills, *self.projects, *self.experience):
            bad = sorted(set(item.tags) - ALLOWED_TAGS)
            if bad:
                raise ValueError(
                    f"unknown tag(s) {bad}; allowed tags are {sorted(ALLOWED_TAGS)}"
                )
        return self


def load_resume(path: str | Path) -> Resume:
    p = Path(path)
    if not p.exists():
        raise ValueError(
            f"resume file not found: {p}. Copy data/resume.example.yaml to {p} "
            "and fill in your details."
        )
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"resume file {p} is not a YAML mapping")
    try:
        return Resume.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"invalid resume {p}: {exc}") from exc
```

- [ ] **Step 8: Create the committed template**

Create `data/resume.example.yaml` with the same structure as the fixture but placeholder content (this is the file a user copies to `data/resume.yaml`):

```yaml
# Copy this file to data/resume.yaml and fill in your real details.
# The AI may only SELECT and REPHRASE what appears here — it never invents.
# Allowed tags on skills/projects/experience: web, ai, data
profile:
  name: Your Name
  email: you@example.com
  phone: ""
  location: City, Country
  headline: Software Engineer
links:
  github: https://github.com/you
  portfolio: ""
  linkedin: ""
education:
  - school: Your University
    degree: B.Tech, Computer Science
    year: "2025"
    detail: ""
skills:
  - { name: Python, tags: [web, ai, data] }
projects:
  - id: proj1
    name: Example Project
    tags: [ai]
    summary: One line on what it does.
    impact:
      - Quantified result or outcome.
    link: ""
experience:
  - id: exp1
    org: Company
    role: Intern
    dates: Summer 2024
    tags: [web]
    impact:
      - What you built and its impact.
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_resume_schema.py -v`
Expected: PASS (all 5).

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml app/config.py .gitignore app/draft/__init__.py app/draft/resume_schema.py data/resume.example.yaml tests/fixtures/resume_min.yaml tests/test_resume_schema.py
git commit -m "feat: resume.yaml schema + validating loader for AI drafting"
```

---

## Task 2: Claude drafting call + id guardrail

**Files:**
- Create: `app/draft/claude_draft.py`
- Test: `tests/test_claude_draft.py`

**Interfaces:**
- Consumes: `Resume` from `app.draft.resume_schema`; `Startup`, `Contact` from `app.models`; `get_settings` from `app.config`.
- Produces: `DraftPlan` (fields: `mode: Literal["formal","casual"]`, `angle: Literal["swe","ai","data"]`, `experience_ids: list[str]`, `project_ids: list[str]`, `summary: str`, `skill_order: list[str]`, `subject: str`, `body: str`); `MalformedDraftError(Exception)`; `unknown_ids(plan: DraftPlan, resume: Resume) -> list[str]`; `build_prompt(startup, contact, resume) -> str`; `draft_plan(startup, contact, resume, *, client=None) -> DraftPlan`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_claude_draft.py`:

```python
import pytest

from app.draft.claude_draft import (
    DraftPlan,
    MalformedDraftError,
    draft_plan,
    unknown_ids,
)
from app.draft.resume_schema import load_resume
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "resume_min.yaml"


class _Resp:
    def __init__(self, parsed):
        self.parsed_output = parsed


class _Messages:
    def __init__(self, outputs):
        self._outputs = list(outputs)
        self.calls = 0

    def parse(self, **kwargs):
        out = self._outputs[self.calls]
        self.calls += 1
        return _Resp(out)


class _Client:
    def __init__(self, outputs):
        self.messages = _Messages(outputs)


class _Startup:
    name = "Globex"
    description = "AI infra for teams"
    industry = "devtools"
    team_size = 8


class _Contact:
    name = "Priya Nair"
    role = "CTO"


def _plan(**over):
    base = dict(mode="formal", angle="ai", experience_ids=["e_intern"],
                project_ids=["p_ai"], summary="s", skill_order=["Python"],
                subject="Intern application", body="hi")
    base.update(over)
    return DraftPlan(**base)


def test_draft_plan_returns_parsed():
    client = _Client([_plan()])
    plan = draft_plan(_Startup(), _Contact(), load_resume(FIXTURE), client=client)
    assert plan.mode == "formal" and plan.project_ids == ["p_ai"]
    assert client.messages.calls == 1


def test_draft_plan_retries_then_raises_on_malformed():
    client = _Client([None, None])
    with pytest.raises(MalformedDraftError):
        draft_plan(_Startup(), _Contact(), load_resume(FIXTURE), client=client)
    assert client.messages.calls == 2  # one retry


def test_draft_plan_retry_succeeds():
    client = _Client([None, _plan()])
    plan = draft_plan(_Startup(), _Contact(), load_resume(FIXTURE), client=client)
    assert plan.mode == "formal"
    assert client.messages.calls == 2


def test_unknown_ids_flags_invented_ids():
    resume = load_resume(FIXTURE)
    good = _plan(experience_ids=["e_intern"], project_ids=["p_ai"])
    bad = _plan(experience_ids=["e_intern"], project_ids=["p_ai", "ghost"])
    assert unknown_ids(good, resume) == []
    assert unknown_ids(bad, resume) == ["ghost"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_claude_draft.py -v`
Expected: FAIL (`app.draft.claude_draft` does not exist).

- [ ] **Step 3: Implement the drafting call**

Create `app/draft/claude_draft.py`:

```python
import json
from typing import Literal

import anthropic
from pydantic import BaseModel

from app.config import get_settings
from app.draft.resume_schema import Resume

MAX_TOKENS = 2048


class DraftPlan(BaseModel):
    mode: Literal["formal", "casual"]
    angle: Literal["swe", "ai", "data"]
    experience_ids: list[str]
    project_ids: list[str]
    summary: str
    skill_order: list[str]
    subject: str
    body: str


class MalformedDraftError(Exception):
    """The Claude response could not be parsed into a DraftPlan after a retry."""


def unknown_ids(plan: DraftPlan, resume: Resume) -> list[str]:
    known = {p.id for p in resume.projects} | {e.id for e in resume.experience}
    return [i for i in (*plan.experience_ids, *plan.project_ids) if i not in known]


_PROMPT = (
    "You are drafting a concise internship-outreach email from a candidate to a "
    "startup, plus the plan for a tailored one-page resume.\n\n"
    "STARTUP:\n  name: {name}\n  description: {description}\n  industry: {industry}\n"
    "  team_size: {team_size}\n\n"
    "CONTACT (address the email to this person):\n  name: {contact_name}\n"
    "  role: {contact_role}\n\n"
    "CANDIDATE RESUME (JSON — you may ONLY select and rephrase from this; NEVER "
    "invent projects, numbers, or experience):\n{resume_json}\n\n"
    "Decide an outreach mode:\n"
    "  formal — a tailored email (< ~150 words) with a resume PDF attached.\n"
    "  casual — a 3-5 sentence conversational note referencing what the startup "
    "builds; no attachment.\n"
    "Suggest the mode from signals (team size, whether the contact is a founder/CTO "
    "vs a generic inbox, brand tone).\n\n"
    "Choose an angle (swe / ai / data) that best fits the startup. Select the "
    "experience_ids and project_ids (from the resume's ids) most relevant to that "
    "angle. Rewrite a 2-line summary. Reorder the skill names for emphasis "
    "(skill_order). Write an email subject and a body matching the mode: plain "
    "tone, reference something concrete about the startup, no flattery."
)


def build_prompt(startup, contact, resume: Resume) -> str:
    team = startup.team_size if getattr(startup, "team_size", None) is not None else "unknown"
    return _PROMPT.format(
        name=startup.name,
        description=getattr(startup, "description", "") or "",
        industry=getattr(startup, "industry", "") or "",
        team_size=team,
        contact_name=getattr(contact, "name", None) or "there",
        contact_role=getattr(contact, "role", "") or "",
        resume_json=json.dumps(resume.model_dump(), ensure_ascii=False),
    )


def draft_plan(startup, contact, resume: Resume, *, client=None) -> DraftPlan:
    client = client or anthropic.Anthropic()
    model = get_settings().anthropic_model
    prompt = build_prompt(startup, contact, resume)
    for attempt in range(2):
        content = prompt
        if attempt == 1:
            content += ("\n\nYour previous response could not be parsed. Respond "
                        "again, strictly matching the required schema.")
        response = client.messages.parse(
            model=model,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": content}],
            output_format=DraftPlan,
        )
        if response.parsed_output is not None:
            return response.parsed_output
    raise MalformedDraftError(
        f"draft response for {startup.name!r} could not be parsed after a retry"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_claude_draft.py -v`
Expected: PASS (all 4).

- [ ] **Step 5: Commit**

```bash
git add app/draft/claude_draft.py tests/test_claude_draft.py
git commit -m "feat: structured Claude drafting call + resume-id guardrail"
```

---

## Task 3: ReportLab resume rendering

**Files:**
- Create: `app/draft/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `DraftPlan` from `app.draft.claude_draft`; `Resume` from `app.draft.resume_schema`.
- Produces: `render_resume(plan: DraftPlan, resume: Resume, startup_name: str, *, out_dir: Path = Path("out/resumes")) -> Path`. Writes a PDF containing the tailored summary, the selected experience (in `experience_ids` order), the selected projects (in `project_ids` order), skills in `skill_order`, and education. Returns the file path. Never called for casual mode.

- [ ] **Step 1: Write the failing test**

Create `tests/test_render.py`:

```python
from pathlib import Path

from pypdf import PdfReader

from app.draft.claude_draft import DraftPlan
from app.draft.render import render_resume
from app.draft.resume_schema import load_resume

FIXTURE = Path(__file__).parent / "fixtures" / "resume_min.yaml"


def _pdf_text(path):
    return "".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


def test_render_includes_selected_and_omits_unselected(tmp_path):
    resume = load_resume(FIXTURE)
    plan = DraftPlan(
        mode="formal", angle="ai",
        experience_ids=["e_intern"], project_ids=["p_ai"],
        summary="Tailored AI infrastructure summary line.",
        skill_order=["PyTorch", "Python"],
        subject="s", body="b",
    )
    path = render_resume(plan, resume, "Globex", out_dir=tmp_path)

    assert path.exists()
    assert path.parent == tmp_path
    text = _pdf_text(path)
    assert "Vaibhav" in text            # header
    assert "Tailored" in text           # tailored summary
    assert "RagPipeline" in text        # selected project
    assert "Acme" in text               # selected experience (org)
    assert "ShopFront" not in text      # unselected project omitted


def test_render_filename_is_sanitized(tmp_path):
    resume = load_resume(FIXTURE)
    plan = DraftPlan(mode="formal", angle="swe", experience_ids=[],
                     project_ids=["p_ai"], summary="x", skill_order=[],
                     subject="s", body="b")
    path = render_resume(plan, resume, "A/B Corp: Inc.", out_dir=tmp_path)
    assert path.exists()
    assert "/" not in path.name and ":" not in path.name
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_render.py -v`
Expected: FAIL (`app.draft.render` does not exist).

- [ ] **Step 3: Implement the renderer**

Create `app/draft/render.py`:

```python
import re
from pathlib import Path

from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from app.draft.claude_draft import DraftPlan
from app.draft.resume_schema import Resume


def _safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_") or "x"


def _order_skills(resume: Resume, skill_order: list[str]) -> list[str]:
    names = [s.name for s in resume.skills]
    ordered = [n for n in skill_order if n in names]
    ordered += [n for n in names if n not in ordered]  # append any not mentioned
    return ordered


def render_resume(plan: DraftPlan, resume: Resume, startup_name: str, *,
                  out_dir: Path = Path("out/resumes")) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{_safe(resume.profile.name)}_Resume_{_safe(startup_name)}.pdf"

    styles = getSampleStyleSheet()
    h_name = ParagraphStyle("hname", parent=styles["Title"], fontSize=18, spaceAfter=2)
    h_sec = ParagraphStyle("hsec", parent=styles["Heading2"], fontSize=12, spaceBefore=8, spaceAfter=2)
    body = ParagraphStyle("body", parent=styles["Normal"], fontSize=10, leading=13, alignment=TA_LEFT)
    bullet = ParagraphStyle("bullet", parent=body, leftIndent=10)

    proj_by_id = {p.id: p for p in resume.projects}
    exp_by_id = {e.id: e for e in resume.experience}

    story: list = []
    p = resume.profile
    story.append(Paragraph(p.name, h_name))
    if p.headline:
        story.append(Paragraph(p.headline, body))
    contact_bits = [b for b in (p.email, p.phone, p.location) if b]
    if contact_bits:
        story.append(Paragraph(" · ".join(contact_bits), body))
    link_bits = [b for b in (resume.links.github, resume.links.portfolio) if b]
    if link_bits:
        story.append(Paragraph(" · ".join(link_bits), body))

    if plan.summary:
        story.append(Paragraph("Summary", h_sec))
        story.append(Paragraph(plan.summary, body))

    selected_exp = [exp_by_id[i] for i in plan.experience_ids if i in exp_by_id]
    if selected_exp:
        story.append(Paragraph("Experience", h_sec))
        for e in selected_exp:
            head = " — ".join(b for b in (e.org, e.role, e.dates) if b)
            story.append(Paragraph(head, body))
            for line in e.impact:
                story.append(Paragraph(f"• {line}", bullet))

    selected_proj = [proj_by_id[i] for i in plan.project_ids if i in proj_by_id]
    if selected_proj:
        story.append(Paragraph("Projects", h_sec))
        for pr in selected_proj:
            head = pr.name if not pr.summary else f"{pr.name} — {pr.summary}"
            story.append(Paragraph(head, body))
            for line in pr.impact:
                story.append(Paragraph(f"• {line}", bullet))

    ordered_skills = _order_skills(resume, plan.skill_order)
    if ordered_skills:
        story.append(Paragraph("Skills", h_sec))
        story.append(Paragraph(", ".join(ordered_skills), body))

    if resume.education:
        story.append(Paragraph("Education", h_sec))
        for ed in resume.education:
            line = " — ".join(b for b in (ed.school, ed.degree, ed.year) if b)
            story.append(Paragraph(line, body))
            if ed.detail:
                story.append(Paragraph(ed.detail, bullet))

    story.append(Spacer(1, 4 * mm))
    SimpleDocTemplate(str(path), pagesize=A4,
                      topMargin=16 * mm, bottomMargin=16 * mm,
                      leftMargin=18 * mm, rightMargin=18 * mm).build(story)
    return path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_render.py -v`
Expected: PASS (both). If a multi-word assertion is split across text runs by the extractor, keep assertions on single distinctive tokens (as written).

- [ ] **Step 5: Commit**

```bash
git add app/draft/render.py tests/test_render.py
git commit -m "feat: ReportLab tailored-resume renderer"
```

---

## Task 4: Service orchestration (draft_startup / draft_all)

**Files:**
- Create: `app/draft/service.py`
- Test: `tests/test_draft_service.py`

**Interfaces:**
- Consumes: `draft_plan`, `unknown_ids`, `MalformedDraftError`, `DraftPlan` from `app.draft.claude_draft`; `render_resume` from `app.draft.render`; `Resume` from `app.draft.resume_schema`; `is_founder_role` from `app.enrich.ranking`; models `Contact`, `Draft`, `DraftMode`, `DraftStatus`, `Event`, `Startup`, `StartupStatus`.
- Produces: `DraftResult(startup_id: int, drafted: bool, mode: str | None)`; `select_primary_contact(contacts: list[Contact]) -> Contact | None`; `draft_startup(session, startup, *, resume, drafter=draft_plan, renderer=render_resume, out_dir=Path("out/resumes")) -> DraftResult`; `draft_all(session, *, limit=50, resume, drafter=draft_plan, renderer=render_resume, out_dir=Path("out/resumes")) -> list[DraftResult]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_draft_service.py`:

```python
from pathlib import Path

import anthropic
from sqlalchemy import select

from app.draft.claude_draft import DraftPlan, MalformedDraftError
from app.draft.resume_schema import load_resume
from app.draft.service import draft_all, select_primary_contact
from app.models import Contact, Draft, Event, Startup, StartupStatus

FIXTURE = Path(__file__).parent / "fixtures" / "resume_min.yaml"


def _resume():
    return load_resume(FIXTURE)


def _enriched(session, name, domain, *, found_via="scraped", role="CTO"):
    s = Startup(name=name, domain=domain, source="yc", status=StartupStatus.ENRICHED)
    session.add(s)
    session.commit()
    session.add(Contact(startup_id=s.id, name="Contact " + name, role=role,
                        email=f"c@{domain}", found_via=found_via, confidence=0.9,
                        verified=True))
    session.commit()
    return s


def _plan(mode="formal", **over):
    base = dict(mode=mode, angle="ai", experience_ids=["e_intern"],
                project_ids=["p_ai"], summary="s", skill_order=["Python"],
                subject="Intern application", body="hi")
    base.update(over)
    return DraftPlan(**base)


def _renderer(plan, resume, startup_name, *, out_dir):
    return Path(out_dir) / f"{startup_name}.pdf"


def test_select_primary_prefers_founder_then_source():
    generic = Contact(role="", email="a@x.io", found_via="generic", confidence=0.5, verified=False)
    founder = Contact(role="CEO", email="b@x.io", found_via="scraped", confidence=0.8, verified=True)
    assert select_primary_contact([generic, founder]) is founder
    assert select_primary_contact([]) is None


def test_draft_all_only_enriched_without_draft(session):
    session.add(Startup(name="Disc", domain="d.io", source="yc",
                        status=StartupStatus.DISCOVERED))
    _enriched(session, "Fresh", "fresh.io")
    already = _enriched(session, "Done", "done.io")
    session.add(Draft(startup_id=already.id, subject="x", body="y"))
    session.commit()

    results = draft_all(session, resume=_resume(),
                        drafter=lambda s, c, r: _plan(), renderer=_renderer)

    assert len(results) == 1 and results[0].drafted is True
    fresh = session.scalars(select(Startup).where(Startup.name == "Fresh")).one()
    assert fresh.status == StartupStatus.DRAFTED
    draft = session.scalars(select(Draft).where(Draft.startup_id == fresh.id)).one()
    assert draft.resume_pdf_path is not None  # formal → PDF


def test_draft_all_casual_has_no_pdf(session):
    _enriched(session, "Casual", "casual.io")
    draft_all(session, resume=_resume(),
              drafter=lambda s, c, r: _plan(mode="casual"), renderer=_renderer)
    draft = session.scalars(select(Draft)).one()
    assert draft.mode.value == "casual" and draft.resume_pdf_path is None


def test_draft_all_contains_malformed(session):
    a = _enriched(session, "A", "a.io")
    _enriched(session, "B", "b.io")

    def drafter(s, c, r):
        if s.name == "A":
            raise MalformedDraftError("bad")
        return _plan()

    results = draft_all(session, resume=_resume(), drafter=drafter, renderer=_renderer)
    assert len(results) == 2
    a_events = session.scalars(
        select(Event).where(Event.startup_id == a.id, Event.kind == "draft_failed")).all()
    assert any(e.payload["reason"] == "malformed_response" for e in a_events)
    assert session.scalars(select(Draft)).all()  # B still drafted


def test_draft_all_contains_invalid_id(session):
    s = _enriched(session, "Ghosty", "ghost.io")
    draft_all(session, resume=_resume(),
              drafter=lambda st, c, r: _plan(project_ids=["p_ai", "ghost"]),
              renderer=_renderer)
    events = session.scalars(
        select(Event).where(Event.startup_id == s.id, Event.kind == "draft_failed")).all()
    assert any(e.payload["reason"] == "invalid_id" for e in events)
    assert session.scalars(select(Draft)).all() == []  # guardrail blocked the draft


def test_draft_all_contains_provider_error(session):
    a = _enriched(session, "A", "a.io")
    _enriched(session, "B", "b.io")

    def drafter(s, c, r):
        if s.name == "A":
            raise anthropic.AnthropicError("api down")
        return _plan()

    results = draft_all(session, resume=_resume(), drafter=drafter, renderer=_renderer)
    assert len(results) == 2
    a_events = session.scalars(
        select(Event).where(Event.startup_id == a.id, Event.kind == "draft_failed")).all()
    assert any(e.payload["reason"] == "provider_error" for e in a_events)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_draft_service.py -v`
Expected: FAIL (`app.draft.service` does not exist).

- [ ] **Step 3: Implement the service**

Create `app/draft/service.py`:

```python
from dataclasses import dataclass
from pathlib import Path

import anthropic
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.draft.claude_draft import DraftPlan, MalformedDraftError, draft_plan, unknown_ids
from app.draft.render import render_resume
from app.draft.resume_schema import Resume
from app.enrich.ranking import is_founder_role
from app.models import (
    Contact, Draft, DraftMode, DraftStatus, Event, Startup, StartupStatus,
)

# Same source ordering as Phase 2 ranking: scraped > api > pattern_guess > generic.
_SOURCE_RANK = {"scraped": 3, "api": 2, "pattern_guess": 1, "generic": 0}


@dataclass
class DraftResult:
    startup_id: int
    drafted: bool
    mode: str | None


def _contact_key(c: Contact) -> tuple:
    return (
        1 if is_founder_role(c.role) else 0,
        _SOURCE_RANK.get(c.found_via, 0),
        1 if c.verified else 0,
        c.confidence,
    )


def select_primary_contact(contacts: list[Contact]) -> Contact | None:
    usable = [c for c in contacts if c.email]
    return max(usable, key=_contact_key) if usable else None


def _log_failed(session: Session, startup_id: int, reason: str, **extra) -> None:
    session.add(Event(startup_id=startup_id, kind="draft_failed",
                      payload={"reason": reason, **extra}))
    session.commit()


def draft_startup(session: Session, startup: Startup, *, resume: Resume,
                  drafter=draft_plan, renderer=render_resume,
                  out_dir: Path = Path("out/resumes")) -> DraftResult:
    contacts = session.scalars(
        select(Contact).where(Contact.startup_id == startup.id)).all()
    contact = select_primary_contact(list(contacts))
    if contact is None:
        _log_failed(session, startup.id, "no_contact")
        return DraftResult(startup.id, False, None)

    try:
        plan: DraftPlan = drafter(startup, contact, resume)
    except MalformedDraftError as exc:
        _log_failed(session, startup.id, "malformed_response", detail=str(exc))
        return DraftResult(startup.id, False, None)

    bad = unknown_ids(plan, resume)
    if bad:
        _log_failed(session, startup.id, "invalid_id", ids=bad)
        return DraftResult(startup.id, False, None)

    pdf_path = None
    if plan.mode == "formal":
        pdf_path = str(renderer(plan, resume, startup.name, out_dir=out_dir))

    session.add(Draft(
        startup_id=startup.id, contact_id=contact.id, mode=DraftMode(plan.mode),
        subject=plan.subject, body=plan.body, resume_pdf_path=pdf_path,
        status=DraftStatus.PENDING_REVIEW,
    ))
    startup.status = StartupStatus.DRAFTED
    session.add(Event(startup_id=startup.id, kind="drafted",
                      payload={"mode": plan.mode, "angle": plan.angle}))
    session.commit()
    return DraftResult(startup.id, True, plan.mode)


def draft_all(session: Session, *, limit: int = 50, resume: Resume,
              drafter=draft_plan, renderer=render_resume,
              out_dir: Path = Path("out/resumes")) -> list[DraftResult]:
    startups = session.scalars(
        select(Startup)
        .where(Startup.status == StartupStatus.ENRICHED,
               Startup.id.not_in(select(Draft.startup_id)))
        .limit(limit)
    ).all()
    results: list[DraftResult] = []
    for startup in startups:
        sid = startup.id  # capture before any rollback expires the instance
        try:
            results.append(draft_startup(
                session, startup, resume=resume, drafter=drafter,
                renderer=renderer, out_dir=out_dir))
        except (anthropic.AnthropicError, ValueError) as exc:
            session.rollback()
            _log_failed(session, sid, "provider_error", detail=str(exc))
            results.append(DraftResult(sid, False, None))
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_draft_service.py -v`
Expected: PASS (all 6).

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python -m pytest`
Expected: all prior tests + the new drafting tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/draft/service.py tests/test_draft_service.py
git commit -m "feat: idempotent draft orchestration with per-startup containment"
```

---

## Task 5: CLI commands (draft, drafts list, drafts show)

**Files:**
- Modify: `app/cli.py`
- Test: `tests/test_cli_draft.py`

**Interfaces:**
- Consumes: `draft_all`, `draft_startup` from `app.draft.service`; `draft_plan`, `select_primary_contact` (for `--dry-run`) — import `draft_plan` from `app.draft.claude_draft` and `select_primary_contact` from `app.draft.service`; `load_resume` from `app.draft.resume_schema`; models `Draft`, `Startup`.
- Produces CLI commands: `m2s draft [--limit N] [--startup DOMAIN] [--dry-run]`, `m2s drafts list`, `m2s drafts show <draft_id>`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_draft.py`:

```python
import shutil
from pathlib import Path

from sqlalchemy import select
from typer.testing import CliRunner

import app.cli as cli_mod
from app.cli import app
from app.db import get_engine, init_db, make_session
from app.draft.claude_draft import DraftPlan
from app.draft.service import DraftResult
from app.models import Contact, Draft, Startup, StartupStatus

runner = CliRunner()
FIXTURE = Path(__file__).parent / "fixtures" / "resume_min.yaml"


def _prepare(monkeypatch, tmp_path, *, with_resume=True):
    db = tmp_path / "test.db"
    monkeypatch.setenv("M2S_DB_PATH", str(db))
    if with_resume:
        resume = tmp_path / "resume.yaml"
        shutil.copy(FIXTURE, resume)
        monkeypatch.setenv("M2S_RESUME_PATH", str(resume))
    else:
        monkeypatch.setenv("M2S_RESUME_PATH", str(tmp_path / "missing.yaml"))
    runner.invoke(app, ["init-db"])
    return db


def test_draft_missing_resume_exits(monkeypatch, tmp_path):
    _prepare(monkeypatch, tmp_path, with_resume=False)
    result = runner.invoke(app, ["draft"])
    assert result.exit_code == 1
    assert "resume file not found" in result.output


def test_draft_reports_summary(monkeypatch, tmp_path):
    _prepare(monkeypatch, tmp_path)

    def fake_draft_all(session, **kwargs):
        return [DraftResult(1, True, "formal"), DraftResult(2, False, None)]

    monkeypatch.setattr(cli_mod, "draft_all", fake_draft_all)
    result = runner.invoke(app, ["draft", "--limit", "5"])
    assert result.exit_code == 0
    assert "processed=2" in result.output and "drafted=1" in result.output


def test_drafts_list_and_show(monkeypatch, tmp_path):
    db = _prepare(monkeypatch, tmp_path)
    engine = get_engine(db)
    init_db(engine)
    with make_session(engine) as s:
        startup = Startup(name="Globex", domain="globex.io", source="yc",
                          status=StartupStatus.DRAFTED)
        s.add(startup)
        s.commit()
        s.add(Draft(startup_id=startup.id, subject="Intern application",
                    body="Hello there", resume_pdf_path="out/resumes/x.pdf"))
        s.commit()
        draft_id = s.scalars(select(Draft.id)).one()

    listed = runner.invoke(app, ["drafts", "list"])
    assert listed.exit_code == 0 and "Globex" in listed.output

    shown = runner.invoke(app, ["drafts", "show", str(draft_id)])
    assert shown.exit_code == 0
    assert "Intern application" in shown.output and "Hello there" in shown.output


def test_draft_dry_run_writes_nothing(monkeypatch, tmp_path):
    db = _prepare(monkeypatch, tmp_path)
    engine = get_engine(db)
    init_db(engine)
    with make_session(engine) as s:
        startup = Startup(name="Globex", domain="globex.io", source="yc",
                          status=StartupStatus.ENRICHED)
        s.add(startup)
        s.commit()
        s.add(Contact(startup_id=startup.id, name="Priya", role="CTO",
                      email="priya@globex.io", found_via="scraped",
                      confidence=0.9, verified=True))
        s.commit()

    plan = DraftPlan(mode="formal", angle="ai", experience_ids=["e_intern"],
                     project_ids=["p_ai"], summary="s", skill_order=["Python"],
                     subject="Preview subject", body="b")
    monkeypatch.setattr(cli_mod, "draft_plan", lambda s, c, r: plan)

    result = runner.invoke(app, ["draft", "--dry-run"])
    assert result.exit_code == 0
    assert "Preview subject" in result.output
    with make_session(engine) as s:
        assert s.scalars(select(Draft)).all() == []  # dry-run persisted nothing
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_cli_draft.py -v`
Expected: FAIL (commands not defined).

- [ ] **Step 3: Add imports and the `draft` command to `app/cli.py`**

Add near the existing imports:

```python
from app.draft.claude_draft import draft_plan
from app.draft.resume_schema import load_resume
from app.draft.service import DraftResult, draft_all, draft_startup, select_primary_contact
from app.models import Contact, Draft, Startup  # extend the existing models import
```

(Extend the existing `from app.models import ...` line rather than duplicating it — add `Draft` and `Contact` to it.)

Add the command:

```python
@app.command()
def draft(
    limit: int = typer.Option(50, help="Max enriched startups to draft"),
    startup: str = typer.Option(None, "--startup", help="Only draft this single domain"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview plans; write nothing"),
):
    """Draft tailored emails (+ resume PDFs) for enriched startups."""
    settings = get_settings()
    try:
        resume = load_resume(settings.resume_path)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)

    with _session() as session:
        if dry_run:
            query = select(Startup).where(Startup.status == StartupStatus.ENRICHED)
            if startup:
                query = query.where(Startup.domain == startup)
            for s in session.scalars(query.limit(limit)).all():
                contacts = session.scalars(
                    select(Contact).where(Contact.startup_id == s.id)).all()
                contact = select_primary_contact(list(contacts))
                if contact is None:
                    typer.echo(f"  {s.name}: no usable contact")
                    continue
                plan = draft_plan(s, contact, resume)
                typer.echo(f"  {s.name} [{plan.mode}/{plan.angle}] {plan.subject}")
            return
        if startup:
            s = session.scalars(select(Startup).where(Startup.domain == startup)).first()
            if s is None:
                typer.echo(f"No startup with domain {startup}", err=True)
                raise typer.Exit(code=1)
            results = [draft_startup(session, s, resume=resume)]
        else:
            results = draft_all(session, limit=limit, resume=resume)

    drafted = sum(1 for r in results if r.drafted)
    typer.echo(f"draft: processed={len(results)} drafted={drafted}")
```

Add `from app.models import StartupStatus` to the imports if not already present (extend the existing models import line).

- [ ] **Step 4: Add the `drafts` inspection sub-app**

Append to `app/cli.py` (before `if __name__ == "__main__":`):

```python
drafts_app = typer.Typer(help="Inspect generated drafts")
app.add_typer(drafts_app, name="drafts")


@drafts_app.command("list")
def drafts_list():
    """List pending drafts."""
    with _session() as session:
        rows = session.execute(
            select(Draft, Startup.name)
            .join(Startup, Draft.startup_id == Startup.id)
        ).all()
    if not rows:
        typer.echo("No drafts.")
        return
    for draft_row, startup_name in rows:
        has_pdf = "y" if draft_row.resume_pdf_path else "n"
        typer.echo(f"  #{draft_row.id} {startup_name} [{draft_row.mode.value}] "
                   f"pdf={has_pdf} — {draft_row.subject}")


@drafts_app.command("show")
def drafts_show(draft_id: int = typer.Argument(..., help="Draft id")):
    """Print a draft's subject, body, and resume PDF path."""
    with _session() as session:
        draft_row = session.get(Draft, draft_id)
        if draft_row is None:
            typer.echo(f"No draft #{draft_id}", err=True)
            raise typer.Exit(code=1)
        typer.echo(f"Draft #{draft_row.id}  mode={draft_row.mode.value}  "
                   f"status={draft_row.status.value}")
        typer.echo(f"Subject: {draft_row.subject}")
        typer.echo(f"PDF: {draft_row.resume_pdf_path or '(none)'}")
        typer.echo("")
        typer.echo(draft_row.body)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_cli_draft.py -v`
Expected: PASS (all 4).

- [ ] **Step 6: Run the full suite**

Run: `.venv/Scripts/python -m pytest`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add app/cli.py tests/test_cli_draft.py
git commit -m "feat: m2s draft + drafts list/show CLI commands"
```

---

## Post-implementation (controller, after all tasks)

- **Manual setup + live smoke test** (surface to the user; live API + real DB): copy `data/resume.example.yaml` → `data/resume.yaml`, fill in real details, ensure `ANTHROPIC_API_KEY` is set, then run `m2s draft --dry-run --limit 1` (preview) and `m2s draft --limit 1` (persist one), then `m2s drafts show <id>` and open the generated PDF under `out/resumes/`.
- Final whole-branch review on the most capable model, then `superpowers:finishing-a-development-branch`.

## Self-Review

- **Spec coverage:** resume.yaml schema + hand-authored template (Task 1) ✓; one structured Claude call → DraftPlan with mode/angle/ids/summary/skill_order/subject/body (Task 2) ✓; hard no-invention guardrail via `unknown_ids` (Tasks 2, 4) ✓; ReportLab formal PDF, casual no-attachment (Tasks 3, 4) ✓; idempotent over enriched-without-draft (Task 4) ✓; per-startup containment mirroring hunt (Task 4) ✓; malformed-JSON one-retry (Task 2) ✓; CLI inspection surface (Task 5) ✓; fully offline tests (all tasks) ✓.
- **Type consistency:** `DraftPlan` fields identical across Tasks 2–5; `draft_plan(startup, contact, resume, *, client=None)`, `render_resume(plan, resume, startup_name, *, out_dir=...)`, `draft_all(session, *, limit, resume, drafter, renderer, out_dir)` signatures match between definition and callers/tests.
- **Placeholder scan:** none — every step carries real code.
