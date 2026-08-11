from app.scraper.email_finder import Person, extract_people

TEAM_HTML = """
<div class="team">
  <div class="member"><h3>Jane Roe</h3><p>Co-Founder &amp; CEO</p></div>
  <div class="member"><h3>Raj Kumar</h3><p>CTO</p></div>
  <div class="member"><h3>Widget Corp</h3><p>Making widgets since 2020</p></div>
  <div class="member"><h3>Priya Nair — Head of Engineering</h3></div>
</div>
"""


def test_extract_people_pairs_name_and_role():
    people = extract_people(TEAM_HTML)
    names = [p.name for p in people]
    assert "Jane Roe" in names
    assert "Raj Kumar" in names
    assert "Priya Nair" in names          # same-line "Name — Role"
    assert "Widget Corp" not in names     # no role keyword -> not a person


def test_extract_people_captures_role():
    people = {p.name: p.role for p in extract_people(TEAM_HTML)}
    assert "founder" in people["Jane Roe"].lower()
    assert people["Raj Kumar"].lower() == "cto"


def test_extract_people_empty_when_no_roles():
    assert extract_people("<p>We build great software for everyone.</p>") == []
