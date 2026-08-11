import pytest

from app.scraper.sources import get_source


CSV_TEXT = """name,website,description,location,industry,founder_names,emails
Acme,https://www.acme.com,Widgets,Bangalore,SaaS,Jane Roe;Raj Kumar,hello@acme.com;jane@acme.com
Beta Labs,beta.io,,,,,
,missing-name.com,,,,,
"""


def test_csv_fetch(tmp_path):
    p = tmp_path / "list.csv"
    p.write_text(CSV_TEXT, encoding="utf-8")
    records = get_source("csv").fetch(path=str(p))
    assert len(records) == 2  # nameless row skipped
    acme = records[0]
    assert acme.domain == "acme.com"
    assert acme.founder_names == ["Jane Roe", "Raj Kumar"]
    assert acme.contact_emails == ["hello@acme.com", "jane@acme.com"]
    assert acme.source == "csv"


def test_csv_requires_path():
    with pytest.raises(ValueError):
        get_source("csv").fetch()


def test_unknown_source():
    with pytest.raises(ValueError, match="csv"):
        get_source("nope")
