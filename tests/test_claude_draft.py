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
