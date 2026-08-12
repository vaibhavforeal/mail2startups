import json
import os
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


def draft_plan(startup, contact, resume: Resume, *, client=None, model=None) -> DraftPlan:
    if client is None:
        client, resolved = resolve_backend()
        model = model or resolved
    else:
        model = model or get_settings().anthropic_model
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
