import dns.resolver

from app.scraper.email_finder import CandidateContact

_DEFAULT_RESOLVER = dns.resolver.Resolver()
_DEFAULT_RESOLVER.lifetime = 5.0
_DEFAULT_RESOLVER.timeout = 5.0


def has_mx(domain: str, resolver=None) -> bool:
    resolver = resolver or _DEFAULT_RESOLVER
    if not domain:
        return False
    for rdtype in ("MX", "A"):
        try:
            answers = resolver.resolve(domain, rdtype)
            if len(list(answers)) > 0:
                return True
        except Exception:
            continue
    return False


def verify_candidates(candidates: list[CandidateContact], resolver=None) -> list[CandidateContact]:
    cache: dict[str, bool] = {}
    for candidate in candidates:
        domain = candidate.email.split("@", 1)[-1]
        if domain not in cache:
            cache[domain] = has_mx(domain, resolver=resolver)
        if cache[domain]:
            candidate.verified = True
    return candidates
