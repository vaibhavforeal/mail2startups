import json

from pydantic import BaseModel

from app.config import get_settings
from app.draft.claude_draft import resolve_backend
from app.draft.resume_schema import Resume

MAX_TOKENS = 512


class FollowupPlan(BaseModel):
    body: str


class MalformedFollowupError(Exception):
    """The Claude response could not be parsed into a FollowupPlan after a retry."""


_PROMPT = (
    "You are writing a SHORT follow-up nudge to a startup that has not replied "
    "to the internship-outreach email below. Keep it to 2-3 sentences, plain "
    "and low-pressure. You may ONLY reference facts already in the original "
    "email or the resume — NEVER invent projects, numbers, or experience.\n\n"
    "ORIGINAL SUBJECT: {subject}\n\n"
    "ORIGINAL EMAIL BODY:\n{body}\n\n"
    "CANDIDATE RESUME (JSON, for grounding only):\n{resume_json}\n\n"
    "Write only the follow-up body (no subject line, no signature block)."
)


def build_prompt(original_subject, original_body, resume: Resume) -> str:
    return _PROMPT.format(
        subject=original_subject,
        body=original_body,
        resume_json=json.dumps(resume.model_dump(), ensure_ascii=False),
    )


def followup_plan(startup, resume: Resume, original_subject, original_body,
                  *, client=None, model=None) -> FollowupPlan:
    if client is None:
        client, resolved = resolve_backend()
        model = model or resolved
    else:
        model = model or get_settings().anthropic_model
    prompt = build_prompt(original_subject, original_body, resume)
    for attempt in range(2):
        content = prompt
        if attempt == 1:
            content += ("\n\nYour previous response could not be parsed. Respond "
                        "again, strictly matching the required schema.")
        response = client.messages.parse(
            model=model,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": content}],
            output_format=FollowupPlan,
        )
        if response.parsed_output is not None:
            return response.parsed_output
    raise MalformedFollowupError(
        f"follow-up response for {startup.name!r} could not be parsed after a retry"
    )
