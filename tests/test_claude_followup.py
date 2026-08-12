from pathlib import Path

import pytest

from app.draft.resume_schema import load_resume
from app.followup.claude_followup import (
    FollowupPlan,
    MalformedFollowupError,
    build_prompt,
    followup_plan,
)

FIXTURE = Path(__file__).parent / "fixtures" / "resume_min.yaml"


class _Resp:
    def __init__(self, parsed):
        self.parsed_output = parsed


class _Messages:
    def __init__(self, outputs):
        self._outputs = list(outputs)
        self.calls = 0
        self.last_kwargs = None

    def parse(self, **kwargs):
        self.last_kwargs = kwargs
        out = self._outputs[self.calls]
        self.calls += 1
        return _Resp(out)


class _Client:
    def __init__(self, outputs):
        self.messages = _Messages(outputs)


class _Startup:
    name = "Globex"


def test_followup_plan_returns_parsed():
    client = _Client([FollowupPlan(body="Just bumping this up.")])
    plan = followup_plan(_Startup(), load_resume(FIXTURE), "Intern application",
                         "Original body", client=client)
    assert plan.body == "Just bumping this up."
    assert client.messages.calls == 1


def test_followup_plan_retries_then_raises():
    client = _Client([None, None])
    with pytest.raises(MalformedFollowupError):
        followup_plan(_Startup(), load_resume(FIXTURE), "S", "B", client=client)
    assert client.messages.calls == 2  # one retry


def test_followup_plan_retry_succeeds():
    client = _Client([None, FollowupPlan(body="bump")])
    plan = followup_plan(_Startup(), load_resume(FIXTURE), "S", "B", client=client)
    assert plan.body == "bump"
    assert client.messages.calls == 2


def test_build_prompt_includes_original_and_grounding():
    prompt = build_prompt("My subject", "My body text", load_resume(FIXTURE))
    assert "My subject" in prompt and "My body text" in prompt
    assert "NEVER invent" in prompt
