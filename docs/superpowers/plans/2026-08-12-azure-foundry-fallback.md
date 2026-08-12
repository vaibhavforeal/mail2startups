# Azure Foundry Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `m2s draft` fall back to a Claude model in Azure AI Foundry when `ANTHROPIC_API_KEY` is absent, via the SDK's `AnthropicFoundry` client, leaving the prompt/parse/guardrail path unchanged.

**Architecture:** Add four optional Foundry settings and one `resolve_backend()` function that returns `(client, model)` — direct Anthropic when its key is set, Azure Foundry when only Foundry creds are set, else a clear error. `draft_plan` delegates client/model selection to it when no client is injected; the `messages.parse(output_format=DraftPlan)` call body is untouched.

**Tech Stack:** Python 3.12+, `anthropic` SDK v0.121.0 (ships `AnthropicFoundry`), pydantic-settings, pytest (offline).

## Global Constraints

- **Fully offline tests:** no test makes a live network/API call. `resolve_backend` tests assert only the constructed client's *type* — SDK client construction performs no I/O. The drafting client stays injectable and mocked, exactly as today.
- **No new third-party dependency:** `anthropic.AnthropicFoundry` is already present in the installed `anthropic` (v0.121.0). Do not add packages.
- **Backend selection is config-time only** — decided from which credentials are present. No runtime failover, no per-request retry across providers.
- **Reuse the structured-output path verbatim:** the `client.messages.parse(model=..., max_tokens=MAX_TOKENS, messages=..., output_format=DraftPlan)` call and its two-attempt retry are unchanged. No second prompt format, no alternate parser.
- **Error containment unchanged:** add no new `try`/`except`. The existing `(anthropic.AnthropicError, ValueError)` tuple in `app/draft/service.py` already contains a `resolve_backend` `ValueError` and any Foundry `BadRequestError`. Never introduce a bare `except`.
- **Injected-client callers keep `anthropic_model`:** when a `client` is passed to `draft_plan`, model resolution stays `get_settings().anthropic_model`. Every existing `tests/test_claude_draft.py` case must remain green, unedited.
- **Commit trailer:** end each commit message with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

### Task 1: Backend resolver (config + `resolve_backend`)

**Files:**
- Modify: `app/config.py` (add four settings after `anthropic_model`)
- Modify: `app/draft/claude_draft.py` (add `import os`; add `resolve_backend`)
- Test: `tests/test_draft_backend.py` (new)

**Interfaces:**
- Consumes: `get_settings()` from `app.config`; `anthropic.Anthropic`, `anthropic.AnthropicFoundry`.
- Produces: `resolve_backend() -> tuple[anthropic.Anthropic, str]` — returns `(client, model)`. Raises `ValueError` when no backend is configured. New `Settings` fields: `azure_foundry_api_key`, `azure_foundry_resource`, `azure_foundry_base_url`, `azure_foundry_model` (all `str = ""`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_draft_backend.py`:

```python
import anthropic
import pytest

from app.config import get_settings
from app.draft.claude_draft import resolve_backend

_FOUNDRY_KEYS = (
    "M2S_AZURE_FOUNDRY_API_KEY",
    "M2S_AZURE_FOUNDRY_RESOURCE",
    "M2S_AZURE_FOUNDRY_BASE_URL",
    "M2S_AZURE_FOUNDRY_MODEL",
)


def _clear_foundry(monkeypatch):
    for k in _FOUNDRY_KEYS:
        monkeypatch.delenv(k, raising=False)


def test_anthropic_key_present_uses_direct_client(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    _clear_foundry(monkeypatch)
    client, model = resolve_backend()
    assert isinstance(client, anthropic.Anthropic)
    assert not isinstance(client, anthropic.AnthropicFoundry)
    assert model == get_settings().anthropic_model


def test_no_anthropic_key_uses_foundry(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _clear_foundry(monkeypatch)
    monkeypatch.setenv("M2S_AZURE_FOUNDRY_API_KEY", "fkey-123")
    monkeypatch.setenv("M2S_AZURE_FOUNDRY_RESOURCE", "my-resource")
    monkeypatch.setenv("M2S_AZURE_FOUNDRY_MODEL", "claude-opus-deploy")
    client, model = resolve_backend()
    assert isinstance(client, anthropic.AnthropicFoundry)
    assert model == "claude-opus-deploy"


def test_foundry_model_defaults_to_anthropic_model(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _clear_foundry(monkeypatch)
    monkeypatch.setenv("M2S_AZURE_FOUNDRY_API_KEY", "fkey-123")
    monkeypatch.setenv("M2S_AZURE_FOUNDRY_RESOURCE", "my-resource")
    client, model = resolve_backend()
    assert isinstance(client, anthropic.AnthropicFoundry)
    assert model == get_settings().anthropic_model


def test_base_url_override_builds_foundry(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _clear_foundry(monkeypatch)
    monkeypatch.setenv("M2S_AZURE_FOUNDRY_API_KEY", "fkey-123")
    monkeypatch.setenv(
        "M2S_AZURE_FOUNDRY_BASE_URL",
        "https://custom.services.ai.azure.com/anthropic",
    )
    client, _ = resolve_backend()
    assert isinstance(client, anthropic.AnthropicFoundry)


def test_no_backend_configured_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _clear_foundry(monkeypatch)
    with pytest.raises(ValueError, match="No drafting backend configured"):
        resolve_backend()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_draft_backend.py -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_backend'`.

- [ ] **Step 3: Add the config settings**

In `app/config.py`, inside `class Settings`, add these four lines immediately after the `anthropic_model` field:

```python
    azure_foundry_api_key: str = ""
    azure_foundry_resource: str = ""
    azure_foundry_base_url: str = ""
    azure_foundry_model: str = ""
```

- [ ] **Step 4: Add `import os` and `resolve_backend`**

In `app/draft/claude_draft.py`, add `import os` at the top of the module (with the other stdlib imports, above `import anthropic`).

Then add this function immediately above `def draft_plan(...)`:

```python
def resolve_backend() -> tuple[anthropic.Anthropic, str]:
    """Pick the drafting client and model.

    Direct Anthropic when ANTHROPIC_API_KEY is set; otherwise Azure AI Foundry
    when a Foundry key and endpoint (resource or base_url) are configured.
    Config-time selection only. Raises ValueError when neither is configured.
    """
    settings = get_settings()
    if os.environ.get("ANTHROPIC_API_KEY"):
        return anthropic.Anthropic(), settings.anthropic_model
    if settings.azure_foundry_api_key and (
        settings.azure_foundry_resource or settings.azure_foundry_base_url
    ):
        kwargs: dict = {"api_key": settings.azure_foundry_api_key}
        if settings.azure_foundry_resource:
            kwargs["resource"] = settings.azure_foundry_resource
        else:
            kwargs["base_url"] = settings.azure_foundry_base_url
        model = settings.azure_foundry_model or settings.anthropic_model
        return anthropic.AnthropicFoundry(**kwargs), model
    raise ValueError(
        "No drafting backend configured: set ANTHROPIC_API_KEY, or "
        "M2S_AZURE_FOUNDRY_API_KEY with M2S_AZURE_FOUNDRY_RESOURCE "
        "(or M2S_AZURE_FOUNDRY_BASE_URL)."
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_draft_backend.py -v`
Expected: PASS (5 passed).

- [ ] **Step 6: Run the full suite (no regressions)**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all prior tests still pass, plus the 5 new ones.

- [ ] **Step 7: Commit**

```bash
git add app/config.py app/draft/claude_draft.py tests/test_draft_backend.py
git commit -m "feat: resolve_backend picks Anthropic or Azure Foundry client

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Wire `draft_plan` to the resolver + docs

**Files:**
- Modify: `app/draft/claude_draft.py` (`draft_plan` signature + delegation)
- Test: `tests/test_draft_backend.py` (append the routing test)
- Modify: `.env.example` (four vars under a Foundry block)
- Modify: `README.md` (a "Drafting backend" subsection)

**Interfaces:**
- Consumes: `resolve_backend()` from Task 1.
- Produces: `draft_plan(startup, contact, resume, *, client=None, model=None) -> DraftPlan` — when `client is None`, client and model come from `resolve_backend()`; when a `client` is injected, `model` defaults to `get_settings().anthropic_model` (unchanged behavior). The parse-loop body is identical to today.

- [ ] **Step 1: Write the failing routing test**

Append to `tests/test_draft_backend.py`:

```python
import types
from pathlib import Path

from app.draft import claude_draft
from app.draft.claude_draft import DraftPlan, draft_plan
from app.draft.resume_schema import load_resume

_FIXTURE = Path(__file__).parent / "fixtures" / "resume_min.yaml"


class _RecordingMessages:
    def __init__(self, plan):
        self._plan = plan
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        return types.SimpleNamespace(parsed_output=self._plan)


class _RecordingClient:
    def __init__(self, plan):
        self.messages = _RecordingMessages(plan)


class _Startup:
    name = "Globex"
    description = "AI infra"
    industry = "devtools"
    team_size = 5


class _Contact:
    name = "Priya Nair"
    role = "CTO"


def test_draft_plan_routes_resolved_model(monkeypatch):
    plan = DraftPlan(
        mode="formal", angle="ai", experience_ids=["e_intern"],
        project_ids=["p_ai"], summary="s", skill_order=["Python"],
        subject="Intern application", body="hi",
    )
    fake = _RecordingClient(plan)
    monkeypatch.setattr(
        claude_draft, "resolve_backend", lambda: (fake, "deployment-x")
    )
    result = draft_plan(_Startup(), _Contact(), load_resume(_FIXTURE), client=None)
    assert result.mode == "formal"
    assert fake.messages.kwargs["model"] == "deployment-x"
```

- [ ] **Step 2: Run the routing test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_draft_backend.py::test_draft_plan_routes_resolved_model -v`
Expected: FAIL — with `client=None` and no `ANTHROPIC_API_KEY`, current `draft_plan` builds a real `anthropic.Anthropic()` (raising, since no key) or ignores the fake; the resolved model `"deployment-x"` is never used.

- [ ] **Step 3: Rewire `draft_plan`**

In `app/draft/claude_draft.py`, replace the `draft_plan` signature and its first two lines. Change:

```python
def draft_plan(startup, contact, resume: Resume, *, client=None) -> DraftPlan:
    client = client or anthropic.Anthropic()
    model = get_settings().anthropic_model
```

to:

```python
def draft_plan(startup, contact, resume: Resume, *, client=None, model=None) -> DraftPlan:
    if client is None:
        client, resolved = resolve_backend()
        model = model or resolved
    else:
        model = model or get_settings().anthropic_model
```

Leave the rest of the function (the `for attempt in range(2)` loop, the `messages.parse` call, the `MalformedDraftError` raise) exactly as-is.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_draft_backend.py tests/test_claude_draft.py -v`
Expected: PASS — the routing test passes and all four existing `test_claude_draft.py` cases still pass unchanged.

- [ ] **Step 5: Update `.env.example`**

Append to `.env.example`:

```
# Azure AI Foundry fallback — used only when ANTHROPIC_API_KEY is unset.
# The deployment must be "Hosted on Anthropic" (Azure-hosted rejects the
# structured-output request that drafting relies on).
M2S_AZURE_FOUNDRY_API_KEY=
M2S_AZURE_FOUNDRY_RESOURCE=
M2S_AZURE_FOUNDRY_BASE_URL=
M2S_AZURE_FOUNDRY_MODEL=
```

- [ ] **Step 6: Update `README.md`**

Insert this section immediately after the Setup code block (after the line ```` ``` ```` that closes Setup, before `## Usage`):

```markdown
## Drafting backend (Anthropic or Azure Foundry)

`m2s draft` picks a Claude backend from whichever credentials are set:

- **`ANTHROPIC_API_KEY` present** → the direct Anthropic API (default).
- **Absent, Foundry configured** → a Claude deployment in Azure AI Foundry
  (`M2S_AZURE_FOUNDRY_API_KEY` + `M2S_AZURE_FOUNDRY_RESOURCE`, deployment name
  in `M2S_AZURE_FOUNDRY_MODEL`; `M2S_AZURE_FOUNDRY_BASE_URL` overrides the
  endpoint if needed).
- **Neither** → `m2s draft` reports a configuration error.

The Foundry deployment must be **"Hosted on Anthropic"** — the "Hosted on
Azure" option rejects the structured-output request drafting relies on.
```

- [ ] **Step 7: Run the full suite**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all tests pass (existing + 6 new backend tests).

- [ ] **Step 8: Commit**

```bash
git add app/draft/claude_draft.py tests/test_draft_backend.py .env.example README.md
git commit -m "feat: draft_plan uses Azure Foundry fallback; document backends

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```
