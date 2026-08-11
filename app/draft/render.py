import re
from pathlib import Path

from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from app.draft.claude_draft import DraftPlan
from app.draft.resume_schema import Resume


def _safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_") or "x"


def _order_skills(resume: Resume, skill_order: list[str]) -> list[str]:
    names = [s.name for s in resume.skills]
    ordered = [n for n in skill_order if n in names]
    ordered += [n for n in names if n not in ordered]  # append any not mentioned
    return ordered


def render_resume(plan: DraftPlan, resume: Resume, startup_name: str, *,
                  out_dir: Path = Path("out/resumes")) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{_safe(resume.profile.name)}_Resume_{_safe(startup_name)}.pdf"

    styles = getSampleStyleSheet()
    h_name = ParagraphStyle("hname", parent=styles["Title"], fontSize=18, spaceAfter=2)
    h_sec = ParagraphStyle("hsec", parent=styles["Heading2"], fontSize=12, spaceBefore=8, spaceAfter=2)
    body = ParagraphStyle("body", parent=styles["Normal"], fontSize=10, leading=13, alignment=TA_LEFT)
    bullet = ParagraphStyle("bullet", parent=body, leftIndent=10)

    proj_by_id = {p.id: p for p in resume.projects}
    exp_by_id = {e.id: e for e in resume.experience}

    story: list = []
    p = resume.profile
    story.append(Paragraph(p.name, h_name))
    if p.headline:
        story.append(Paragraph(p.headline, body))
    contact_bits = [b for b in (p.email, p.phone, p.location) if b]
    if contact_bits:
        story.append(Paragraph(" · ".join(contact_bits), body))
    link_bits = [b for b in (resume.links.github, resume.links.portfolio) if b]
    if link_bits:
        story.append(Paragraph(" · ".join(link_bits), body))

    if plan.summary:
        story.append(Paragraph("Summary", h_sec))
        story.append(Paragraph(plan.summary, body))

    selected_exp = [exp_by_id[i] for i in plan.experience_ids if i in exp_by_id]
    if selected_exp:
        story.append(Paragraph("Experience", h_sec))
        for e in selected_exp:
            head = " — ".join(b for b in (e.org, e.role, e.dates) if b)
            story.append(Paragraph(head, body))
            for line in e.impact:
                story.append(Paragraph(f"• {line}", bullet))

    selected_proj = [proj_by_id[i] for i in plan.project_ids if i in proj_by_id]
    if selected_proj:
        story.append(Paragraph("Projects", h_sec))
        for pr in selected_proj:
            head = pr.name if not pr.summary else f"{pr.name} — {pr.summary}"
            story.append(Paragraph(head, body))
            for line in pr.impact:
                story.append(Paragraph(f"• {line}", bullet))

    ordered_skills = _order_skills(resume, plan.skill_order)
    if ordered_skills:
        story.append(Paragraph("Skills", h_sec))
        story.append(Paragraph(", ".join(ordered_skills), body))

    if resume.education:
        story.append(Paragraph("Education", h_sec))
        for ed in resume.education:
            line = " — ".join(b for b in (ed.school, ed.degree, ed.year) if b)
            story.append(Paragraph(line, body))
            if ed.detail:
                story.append(Paragraph(ed.detail, bullet))

    story.append(Spacer(1, 4 * mm))
    SimpleDocTemplate(str(path), pagesize=A4,
                      topMargin=16 * mm, bottomMargin=16 * mm,
                      leftMargin=18 * mm, rightMargin=18 * mm).build(story)
    return path
