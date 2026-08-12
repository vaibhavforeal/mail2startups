from types import SimpleNamespace

from app.inbox.matching import detect_bounce, match_reply


def _fm(**over):
    base = dict(in_reply_to="", references=[], from_addr="", raw="")
    base.update(over)
    return SimpleNamespace(**base)


SENT = {"<out-1@d.com>": (10, 100), "<out-2@d.com>": (20, 200)}
CONTACTS = {"founder@acme.io": 30}


def test_match_reply_exact_in_reply_to():
    fm = _fm(in_reply_to="<out-1@d.com>", from_addr="x@acme.io")
    assert match_reply(fm, sent_by_message_id=SENT,
                       contact_emails_by_startup=CONTACTS) == (10, 100, "<out-1@d.com>")


def test_match_reply_references_only():
    fm = _fm(references=["<other@z>", "<out-2@d.com>"], from_addr="x@z")
    assert match_reply(fm, sent_by_message_id=SENT,
                       contact_emails_by_startup=CONTACTS) == (20, 200, "<out-2@d.com>")


def test_match_reply_from_address_fallback():
    fm = _fm(from_addr="Founder@Acme.io")
    assert match_reply(fm, sent_by_message_id=SENT,
                       contact_emails_by_startup=CONTACTS) == (30, None, None)


def test_match_reply_no_match_returns_none():
    fm = _fm(from_addr="stranger@nowhere.io")
    assert match_reply(fm, sent_by_message_id=SENT,
                       contact_emails_by_startup=CONTACTS) is None


def test_detect_bounce_mailer_daemon_known_id():
    fm = _fm(from_addr="MAILER-DAEMON@d.com",
             raw="Delivery failed for <out-1@d.com> ... 550 no such user")
    assert detect_bounce(fm, sent_by_message_id=SENT) == (10, 100)


def test_detect_bounce_dsn_body_marker_known_id():
    fm = _fm(from_addr="postmaster@relay.io",
             raw="Content-Type: message/delivery-status\nfailed <out-2@d.com>")
    assert detect_bounce(fm, sent_by_message_id=SENT) == (20, 200)


def test_detect_bounce_ignores_ordinary_mail():
    fm = _fm(from_addr="friend@d.com", raw="hi how are you <out-1@d.com>")
    assert detect_bounce(fm, sent_by_message_id=SENT) is None


def test_detect_bounce_unknown_id_returns_none():
    fm = _fm(from_addr="mailer-daemon@d.com",
             raw="Delivery failed for <unknown@d.com>")
    assert detect_bounce(fm, sent_by_message_id=SENT) is None
