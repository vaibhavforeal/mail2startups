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
