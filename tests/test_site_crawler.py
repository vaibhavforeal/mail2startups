import socket

import httpx
import respx
from httpx import Response

from app.scraper.site_crawler import CrawledPage, crawl_site


def _resolves_public(host):
    return ["93.184.216.34"]  # globally-routable IP so the SSRF guard allows the crawl


@respx.mock
def test_crawl_collects_200_html_pages():
    respx.get("https://acme.com/").mock(return_value=Response(200, html="<h1>Home</h1>"))
    respx.get("https://acme.com/about").mock(return_value=Response(200, html="<h1>About</h1>"))
    respx.get("https://acme.com/team").mock(return_value=Response(404, text="nope"))
    # all other default paths -> 404
    for path in ("/about-us", "/contact", "/careers", "/company"):
        respx.get(f"https://acme.com{path}").mock(return_value=Response(404))

    with httpx.Client() as client:
        pages = crawl_site("acme.com", client=client, resolve_host=_resolves_public)

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
        pages = crawl_site("acme.com", client=client, resolve_host=_resolves_public)
    assert len(pages) == 6  # the erroring root did not abort the rest


def test_crawl_blocks_domain_resolving_to_metadata_ip():
    # A domain that resolves into the cloud-metadata range must never be crawled (SSRF guard).
    def _resolves_metadata(host):
        return ["169.254.169.254"]

    assert crawl_site("evil.internal", resolve_host=_resolves_metadata) == []


def test_crawl_blocks_unresolvable_domain():
    def _fails(host):
        raise socket.gaierror("name or service not known")

    assert crawl_site("nope.invalid", resolve_host=_fails) == []


def test_crawl_blocks_domain_resolving_to_multicast_ip():
    # Multicast IPs report is_global=True, so they need an explicit reject in the guard.
    def _resolves_multicast(host):
        return ["224.0.0.1"]

    assert crawl_site("evil.multicast", resolve_host=_resolves_multicast) == []


@respx.mock
def test_crawl_blocks_redirect_hop_to_metadata_ip():
    # A public site that 30x-redirects to the cloud-metadata host must NOT be
    # followed: the redirect hop is re-validated by the guarded transport.
    respx.get("https://acme.com/").mock(
        return_value=Response(302, headers={"Location": "http://169.254.169.254/"}))
    # If the hop were followed, this secret would be returned as the page body.
    respx.get("http://169.254.169.254/").mock(
        return_value=Response(200, html="<h1>SECRET METADATA</h1>"))
    for path in ("/about", "/about-us", "/team", "/contact", "/careers", "/company"):
        respx.get(f"https://acme.com{path}").mock(return_value=Response(404))

    def resolve(host):
        return ["93.184.216.34"] if host == "acme.com" else [host]  # metadata host is IP-literal

    pages = crawl_site("acme.com", resolve_host=resolve)  # owns_client -> guarded transport
    assert pages == []  # redirect into the metadata IP was refused, not crawled


@respx.mock
def test_crawl_follows_redirect_to_public_host():
    # A legitimate redirect to another globally-routable host is still followed.
    respx.get("https://acme.com/").mock(
        return_value=Response(301, headers={"Location": "https://www.acme.com/"}))
    respx.get("https://www.acme.com/").mock(return_value=Response(200, html="<h1>Home</h1>"))
    for path in ("/about", "/about-us", "/team", "/contact", "/careers", "/company"):
        respx.get(f"https://acme.com{path}").mock(return_value=Response(404))

    def resolve(host):
        return ["93.184.216.34"]  # both apex and www resolve public

    pages = crawl_site("acme.com", resolve_host=resolve)
    assert len(pages) == 1
    assert pages[0].url == "https://acme.com/" and pages[0].html == "<h1>Home</h1>"
