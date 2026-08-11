import ipaddress
import socket
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass

import httpx

DEFAULT_PATHS: tuple[str, ...] = (
    "/", "/about", "/about-us", "/team", "/contact", "/careers", "/company",
)

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

REQUEST_TIMEOUT = 15.0


def _default_resolve_host(host: str) -> list[str]:
    """Resolve a hostname to its IP address strings via the system resolver."""
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


def _is_safe_host(host: str, resolve_host: Callable[[str], list[str]] | None = None) -> bool:
    """True only when every IP `host` resolves to is a globally-routable address.

    SSRF guard: refuses loopback / private / link-local / reserved targets (including the
    cloud-metadata endpoint 169.254.169.254). `resolve_host` is injectable for offline tests.
    """
    resolve_host = resolve_host or _default_resolve_host
    if not host:
        return False
    try:
        addrs = resolve_host(host)
    except Exception:
        return False
    if not addrs:
        return False
    for addr in addrs:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False
        if not ip.is_global or ip.is_multicast:
            return False
    return True


class _SSRFGuardTransport(httpx.BaseTransport):
    """Re-validate every request the client actually dials — including each
    redirect hop — against the SSRF guard, then delegate to a real transport.

    ``follow_redirects=True`` (kept for legitimate www<->apex and http->https
    hops) otherwise lets a crawled page redirect to a private/loopback/metadata
    address that the one-time up-front host check never sees. Checking here, at
    the point httpx opens each connection, closes that redirect-hop gap.
    """

    def __init__(self, inner: httpx.BaseTransport,
                 resolve_host: Callable[[str], list[str]] | None = None):
        self._inner = inner
        self._resolve_host = resolve_host

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if not _is_safe_host(request.url.host, self._resolve_host):
            raise httpx.ConnectError(
                f"SSRF guard refused non-global host: {request.url.host!r}",
                request=request,
            )
        return self._inner.handle_request(request)

    def close(self) -> None:
        self._inner.close()


@dataclass
class CrawledPage:
    url: str
    html: str
    status: int


def _looks_like_html(response: httpx.Response) -> bool:
    ctype = response.headers.get("content-type", "")
    return "html" in ctype.lower() or "<" in response.text[:200]


def crawl_site(domain: str, client: httpx.Client | None = None,
               paths: tuple[str, ...] = DEFAULT_PATHS,
               resolve_host: Callable[[str], list[str]] | None = None) -> list[CrawledPage]:
    # SSRF guard: never crawl a domain that resolves to a non-global (private/loopback/
    # metadata) address. Runs before any client/connection is opened.
    if not _is_safe_host(domain, resolve_host):
        return []

    owns_client = client is None
    if owns_client:
        # Wrap the transport so redirect hops are re-validated too, not just the
        # initial host — a page can 30x-redirect into private/metadata space.
        transport = _SSRFGuardTransport(httpx.HTTPTransport(), resolve_host=resolve_host)
        ctx = httpx.Client(headers={"User-Agent": BROWSER_UA}, timeout=REQUEST_TIMEOUT,
                           follow_redirects=True, transport=transport)
    else:
        ctx = nullcontext(client)

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
