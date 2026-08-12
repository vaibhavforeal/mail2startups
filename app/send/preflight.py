from dataclasses import dataclass


@dataclass
class PreflightReport:
    spf: tuple[bool, str]
    dkim: tuple[bool, str]
    dmarc: tuple[bool, str]

    @property
    def ok(self) -> bool:
        return self.spf[0] and self.dkim[0] and self.dmarc[0]


def _txt_contains(resolve, name: str, marker: str) -> tuple[bool, str]:
    for record in resolve(name, "TXT"):
        if marker.lower() in record.lower():
            return True, record
    return False, f"no TXT at {name} containing {marker!r}"


def check_dns(domain: str, *, selector: str, resolve) -> PreflightReport:
    """resolve(name, 'TXT') -> list[str]. SPF on the domain, DMARC on
    _dmarc.<domain>, DKIM on <selector>._domainkey.<domain> (skipped when
    selector is blank)."""
    spf = _txt_contains(resolve, domain, "v=spf1")
    dmarc = _txt_contains(resolve, f"_dmarc.{domain}", "v=DMARC1")
    if selector:
        dkim = _txt_contains(resolve, f"{selector}._domainkey.{domain}", "v=DKIM1")
    else:
        dkim = (True, "skipped: no M2S_DKIM_SELECTOR set")
    return PreflightReport(spf=spf, dkim=dkim, dmarc=dmarc)
