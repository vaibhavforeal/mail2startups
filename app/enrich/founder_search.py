import re

import httpx

from app.config import get_settings

SEARCH_URL = "https://api.exa.ai/search"

_NAME_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b")

# Capitalized spans that are never a person's name.
_NAME_STOPWORDS = frozenset({
    "The", "Our", "About", "Founder", "Founders", "Co", "Chief", "Team",
    "Acme", "Inc", "Ltd", "Llc", "Startup", "India", "Ceo", "Cto",
})


def _extract_names_from_snippets(snippets: list[str]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for snippet in snippets:
        for match in _NAME_RE.findall(snippet):
            first_word = match.split()[0]
            if first_word in _NAME_STOPWORDS:
                continue
            if match not in seen:
                seen.add(match)
                names.append(match)
    return names


def _exa_search(query: str) -> list[dict]:
    key = get_settings().exa_api_key
    if not key:
        return []
    resp = httpx.post(
        SEARCH_URL,
        headers={"x-api-key": key, "Content-Type": "application/json"},
        json={"query": query, "numResults": 5, "contents": {"text": True}},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


def find_founder_names(startup_name: str, search_fn=None, limit: int = 5) -> list[str]:
    search_fn = search_fn or _exa_search
    query = f"{startup_name} startup founder CEO CTO"
    results = search_fn(query)
    snippets = [
        f"{item.get('title', '')} {item.get('text', '')}".strip()
        for item in results
    ]
    return _extract_names_from_snippets(snippets)[:limit]
