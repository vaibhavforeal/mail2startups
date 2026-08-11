from contextlib import nullcontext
from dataclasses import dataclass

import httpx

DEFAULT_PATHS: tuple[str, ...] = (
    "/", "/about", "/about-us", "/team", "/contact", "/careers", "/company",
)

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

REQUEST_TIMEOUT = 15.0


@dataclass
class CrawledPage:
    url: str
    html: str
    status: int


def _looks_like_html(response: httpx.Response) -> bool:
    ctype = response.headers.get("content-type", "")
    return "html" in ctype.lower() or "<" in response.text[:200]


def crawl_site(domain: str, client: httpx.Client | None = None,
               paths: tuple[str, ...] = DEFAULT_PATHS) -> list[CrawledPage]:
    owns_client = client is None
    ctx = httpx.Client(headers={"User-Agent": BROWSER_UA},
                       timeout=REQUEST_TIMEOUT, follow_redirects=True) if owns_client \
        else nullcontext(client)

    pages: list[CrawledPage] = []
    with ctx as active:
        for path in paths:
            url = f"https://{domain}{path}"
            try:
                response = active.get(url)
            except httpx.HTTPError:
                continue
            if response.status_code == 200 and _looks_like_html(response):
                pages.append(CrawledPage(url=url, html=response.text, status=200))
    return pages
