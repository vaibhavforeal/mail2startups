import httpx
from bs4 import BeautifulSoup

from app.ai import extract_startups_from_text
from app.scraper.sources.base import StartupRecord

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")


class ListicleSource:
    name = "listicle"

    def fetch(self, limit: int = 100, url: str | None = None, **filters) -> list[StartupRecord]:
        if not url:
            raise ValueError("listicle source requires url=<article url>")
        resp = httpx.get(url, headers={"User-Agent": BROWSER_UA},
                         timeout=30, follow_redirects=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        text = " ".join(soup.get_text(separator=" ").split())
        extracted = extract_startups_from_text(text)
        return [
            StartupRecord(
                name=item.name,
                website=item.website,
                description=item.description or "",
                source=self.name,
            )
            for item in extracted[:limit]
        ]
