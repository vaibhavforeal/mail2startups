from app.enrich.founder_search import (
    _extract_names_from_snippets,
    find_founder_names,
)


def test_extract_names_from_snippets():
    names = _extract_names_from_snippets([
        "Jane Roe is the co-founder and CEO of Acme.",
        "Acme was founded by Raj Kumar and Jane Roe.",
        "Our mission is to build widgets.",  # no names
    ])
    assert "Jane Roe" in names
    assert "Raj Kumar" in names
    assert names.count("Jane Roe") == 1  # de-duplicated


def test_find_founder_names_uses_injected_search():
    def fake_search(query):
        assert "Acme" in query
        return [
            {"title": "Jane Roe - Founder, Acme", "text": "Jane Roe leads Acme."},
            {"title": "About", "text": "Co-founded by Raj Kumar."},
        ]

    names = find_founder_names("Acme", search_fn=fake_search)
    assert set(names) >= {"Jane Roe", "Raj Kumar"}


def test_find_founder_names_no_key_no_fn_returns_empty(monkeypatch):
    monkeypatch.delenv("M2S_EXA_API_KEY", raising=False)
    assert find_founder_names("Acme") == []
