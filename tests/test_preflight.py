from app.send.preflight import check_dns


def _resolver(records):
    def resolve(name, rtype):
        return records.get(name, [])
    return resolve


def test_all_pass():
    resolve = _resolver({
        "d.com": ["v=spf1 include:hostinger.com ~all"],
        "_dmarc.d.com": ["v=DMARC1; p=none"],
        "sel._domainkey.d.com": ["v=DKIM1; k=rsa; p=ABC"],
    })
    report = check_dns("d.com", selector="sel", resolve=resolve)
    assert report.ok
    assert report.spf[0] and report.dmarc[0] and report.dkim[0]


def test_missing_spf_and_dmarc_fail():
    resolve = _resolver({"sel._domainkey.d.com": ["v=DKIM1; p=ABC"]})
    report = check_dns("d.com", selector="sel", resolve=resolve)
    assert not report.ok
    assert not report.spf[0] and not report.dmarc[0]
    assert "v=spf1" in report.spf[1]


def test_blank_selector_skips_dkim():
    resolve = _resolver({
        "d.com": ["v=spf1 ~all"],
        "_dmarc.d.com": ["v=DMARC1"],
    })
    report = check_dns("d.com", selector="", resolve=resolve)
    assert report.ok                       # spf+dmarc pass, dkim skipped counts as pass
    assert "skipped" in report.dkim[1]
