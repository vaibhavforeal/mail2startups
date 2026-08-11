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
