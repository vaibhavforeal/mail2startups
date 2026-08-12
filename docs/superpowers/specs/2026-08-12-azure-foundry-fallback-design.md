# Azure Foundry Fallback — Design Spec

**Date:** 2026-08-12
**Status:** Approved by user (brainstorming session)
**Parent spec:** `2026-08-11-phase3-ai-drafting-design.md` (the drafting client)

## Scope

Add an **alternate drafting backend**: when `ANTHROPIC_API_KEY` is absent but
Azure AI Foundry credentials are configured, the drafting call runs against a
Claude model deployed in Azure AI Foundry via the official SDK's
`AnthropicFoundry` client. Everything downstream of the client — the prompt,
the `messages.parse(output_format=DraftPlan)` structured call, the retry, the
guardrail, rendering, persistence — is unchanged.

This is a backend-selection feature only. It does **not** change what a draft
is, how it is stored, or the CLI surface.

## Requirements

- Backend is chosen **at client-construction time**, deterministically, from
  which credentials are present. No mid-run switching, no per-request failover.
- Preference order: **direct Anthropic first** (`ANTHROPIC_API_KEY` in the
  environment), **Azure Foundry second** (Foundry key + endpoint), else a
  clear configuration error.
- The Foundry client targets a **Claude deployment** and reuses the exact same
  `messages.parse` / `output_format` / `parsed_output` structured-output path.
  No second prompt format, no separate parser.
- The model string sent to Foundry is the **deployment name**, configured
  independently of `anthropic_model` (deployment names need not match canonical
  Claude ids), and falls back to `anthropic_model` when unset.
- Fully offline-testable: no test constructs a live client or makes a network
  call; the Claude client stays injectable and mocked, exactly as today.
- No new third-party dependency: `AnthropicFoundry` ships inside the already
  installed `anthropic` package (verified: v0.121.0 exposes it).

## Decisions

| Topic | Decision | Why |
|---|---|---|
| Client class | `anthropic.AnthropicFoundry(resource=… \| base_url=…, api_key=…)` | First-party class in the installed SDK; shares the `messages` resource, so `messages.parse` is byte-for-byte the same call. No base-`Anthropic` + manual-header workaround needed. |
| Trigger | **Config-time** on `ANTHROPIC_API_KEY` presence | User's choice. Simple, deterministic, no half-switched runs. Runtime failover is explicitly out of scope. |
| Endpoint config | `resource` name preferred; optional `base_url` override | `resource="my-resource"` lets the SDK build the URL (`https://<resource>.services.ai.azure.com/anthropic`); `base_url` is an escape hatch for non-standard endpoints. |
| Auth | Static key via `api_key=` | User has a Foundry **key** (not Entra/AAD). Foundry accepts the key as `x-api-key`; no custom header or Bearer token needed. |
| Model | Separate `azure_foundry_model` (deployment name), defaults to `anthropic_model` | Deployment names are user-chosen and need not equal `claude-opus-5`. |
| Azure-hosted caveat | **Documented, not coded around** | Structured outputs return 400 when the deployment is "Hosted on Azure" rather than "Hosted on Anthropic." The fix is choosing the right hosting option at deploy time; a JSON-mode adapter is a separate feature (YAGNI). |

## Architecture

Two edited files, one new test file, two doc files. No new modules.

### `app/config.py` — four new optional settings

```python
azure_foundry_api_key: str = ""     # M2S_AZURE_FOUNDRY_API_KEY
azure_foundry_resource: str = ""    # M2S_AZURE_FOUNDRY_RESOURCE  (e.g. "admin-3848-resource")
azure_foundry_base_url: str = ""    # M2S_AZURE_FOUNDRY_BASE_URL  (optional endpoint override)
azure_foundry_model: str = ""       # M2S_AZURE_FOUNDRY_MODEL      (deployment name)
```

`ANTHROPIC_API_KEY` remains **un-prefixed** (the SDK reads it directly); the
presence check reads `os.environ`, which already includes `.env` values because
`config.py` calls `load_dotenv()` at import.

### `app/draft/claude_draft.py` — a backend resolver

New function, single responsibility — pick the client and the model:

```python
def resolve_backend() -> tuple[anthropic.Anthropic, str]:
    """Return (client, model): direct Anthropic if its key is set, else Azure Foundry."""
    settings = get_settings()
    if os.environ.get("ANTHROPIC_API_KEY"):
        return anthropic.Anthropic(), settings.anthropic_model
    if settings.azure_foundry_api_key and (settings.azure_foundry_resource or settings.azure_foundry_base_url):
        kwargs = {"api_key": settings.azure_foundry_api_key}
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

`draft_plan` gains an optional `model` parameter and delegates client/model
selection to the resolver only when no client is injected:

```python
def draft_plan(startup, contact, resume: Resume, *, client=None, model=None) -> DraftPlan:
    if client is None:
        client, resolved = resolve_backend()
        model = model or resolved
    else:
        model = model or get_settings().anthropic_model
    ...  # prompt build, the two-attempt messages.parse loop — UNCHANGED
```

The `client.messages.parse(model=model, ...)` call body is untouched; `model`
is simply the resolved value instead of a hardcoded `get_settings().anthropic_model`.

## Data Flow

```
draft_startup / draft_all  (unchanged)
   └─ draft_plan(startup, contact, resume)           # client=None in production
        └─ resolve_backend()  ── ANTHROPIC_API_KEY set? ── yes ─▶ (Anthropic(),        anthropic_model)
                                                          └─ no  ─▶ (AnthropicFoundry(), foundry_model)
        └─ client.messages.parse(model=model, output_format=DraftPlan)   # identical either way
```

Injected-client callers (every existing test, and any future explicit
override) bypass `resolve_backend` entirely and keep today's behavior.

## Error Handling

- **Neither backend configured:** `resolve_backend()` raises `ValueError`
  naming both configuration paths. Reaching `draft_plan` through the service,
  this is caught by the existing `(anthropic.AnthropicError, ValueError)`
  containment in `draft_one`/`draft_all` and logged as `provider_error` — the
  batch is not aborted, consistent with today's missing-key behavior.
- **Foundry deployment is "Hosted on Azure":** `messages.parse` raises
  `anthropic.BadRequestError` (400) — a subclass of `anthropic.AnthropicError`,
  so it too is contained and logged as `provider_error`. Documented as a deploy
  requirement, not silently worked around.
- No new bare excepts; the containment tuple is unchanged.

## Testing

New file `tests/test_draft_backend.py`, all offline (monkeypatch env + settings;
never construct a live client that dials out):

1. `ANTHROPIC_API_KEY` set → `resolve_backend()` returns an `anthropic.Anthropic`
   instance and `anthropic_model`.
2. `ANTHROPIC_API_KEY` unset, Foundry key + resource set → returns an
   `anthropic.AnthropicFoundry` instance and the Foundry deployment model.
3. `azure_foundry_model` empty → Foundry path returns `anthropic_model`.
4. Neither configured → raises `ValueError` whose message names both paths.
5. `base_url` set instead of `resource` → Foundry client is built (constructs
   without error), proving the escape hatch.
6. `draft_plan` routing: monkeypatch `resolve_backend` to return
   `(fake_client, "deployment-x")`, call `draft_plan(..., client=None)`, assert
   `fake_client.messages.parse` received `model="deployment-x"`.

Existing `tests/test_claude_draft.py` (injected client) must stay green
unchanged — the regression guard that injected callers are unaffected.

## Documentation

- `.env.example`: add the four `M2S_AZURE_FOUNDRY_*` vars under a new
  "Azure AI Foundry fallback" comment block, with the key blank.
- `README.md`: one short subsection — precedence (Anthropic key wins; Foundry
  used only when it is absent) and the **"Hosted on Anthropic" deployment
  requirement** for structured outputs.

## Out of Scope (YAGNI)

- Runtime failover from Anthropic to Foundry on API errors.
- A tool-use / JSON-mode adapter for "Hosted on Azure" deployments.
- Entra/AAD token auth (`azure_ad_token_provider`) — user has a static key.
- Any change to send/inbox, the dashboard, or the draft schema.
