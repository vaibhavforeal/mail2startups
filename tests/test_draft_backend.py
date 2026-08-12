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

_SDK_FOUNDRY_KEYS = (
    "ANTHROPIC_FOUNDRY_API_KEY",
    "ANTHROPIC_FOUNDRY_RESOURCE",
    "ANTHROPIC_FOUNDRY_BASE_URL",
)


def _clear_foundry(monkeypatch):
    for k in _FOUNDRY_KEYS + _SDK_FOUNDRY_KEYS:
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
