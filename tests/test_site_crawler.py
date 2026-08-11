import httpx
import respx
from httpx import Response

from app.scraper.site_crawler import CrawledPage, crawl_site


@respx.mock
def test_crawl_collects_200_html_pages():
    respx.get("https://acme.com/").mock(return_value=Response(200, html="<h1>Home</h1>"))
    respx.get("https://acme.com/about").mock(return_value=Response(200, html="<h1>About</h1>"))
    respx.get("https://acme.com/team").mock(return_value=Response(404, text="nope"))
    # all other default paths -> 404
    for path in ("/about-us", "/contact", "/careers", "/company"):
        respx.get(f"https://acme.com{path}").mock(return_value=Response(404))

    with httpx.Client() as client:
        pages = crawl_site("acme.com", client=client)

    urls = [p.url for p in pages]
    assert "https://acme.com/" in urls
    assert "https://acme.com/about" in urls
    assert "https://acme.com/team" not in urls  # 404 skipped
    assert all(isinstance(p, CrawledPage) and p.status == 200 for p in pages)


@respx.mock
def test_crawl_swallows_connection_errors():
    respx.get("https://acme.com/").mock(side_effect=httpx.ConnectError("boom"))
    for path in ("/about", "/about-us", "/team", "/contact", "/careers", "/company"):
        respx.get(f"https://acme.com{path}").mock(return_value=Response(200, html="<p>ok</p>"))
    with httpx.Client() as client:
        pages = crawl_site("acme.com", client=client)
    assert len(pages) == 6  # the erroring root did not abort the rest
