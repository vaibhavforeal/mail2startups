import dns.resolver

from app.enrich.verify import has_mx, verify_candidates
from app.scraper.email_finder import CandidateContact


class FakeResolver:
    def __init__(self, good_domains, a_domains=()):
        self.good = set(good_domains)
        self.a = set(a_domains)
        self.calls = []

    def resolve(self, domain, rdtype):
        self.calls.append((domain, rdtype))
        if rdtype == "MX" and domain in self.good:
            return ["10 mail.%s." % domain]
        if rdtype == "A" and domain in self.a:
            return ["1.2.3.4"]
        raise dns.resolver.NXDOMAIN()


def test_has_mx_true_and_false():
    resolver = FakeResolver(good_domains={"acme.com"})
    assert has_mx("acme.com", resolver=resolver) is True
    assert has_mx("nope.invalid", resolver=resolver) is False


def test_verify_candidates_sets_flag_and_caches():
    resolver = FakeResolver(good_domains={"acme.com"})
    cands = [
        CandidateContact(email="jane@acme.com", found_via="pattern_guess", confidence=0.7),
        CandidateContact(email="raj@acme.com", found_via="pattern_guess", confidence=0.6),
        CandidateContact(email="x@ghost.invalid", found_via="pattern_guess", confidence=0.5),
    ]
    out = verify_candidates(cands, resolver=resolver)
    assert out[0].verified is True and out[1].verified is True
    assert out[2].verified is False
    # acme.com resolved once despite two candidates (per-domain cache)
    assert [d for d, _ in resolver.calls].count("acme.com") == 1


def test_has_mx_falls_back_to_a_record():
    resolver = FakeResolver(good_domains=set(), a_domains={"webonly.com"})
    assert has_mx("webonly.com", resolver=resolver) is True
    kinds = [rdtype for _, rdtype in resolver.calls]
    assert "MX" in kinds and "A" in kinds        # tried MX first, then fell back to A
