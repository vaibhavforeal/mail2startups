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
