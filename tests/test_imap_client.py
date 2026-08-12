from app.inbox.imap_client import FetchedMessage, parse_message

RAW_REPLY = b"""From: Priya Nair <priya@globex.io>
To: me@d.com
Subject: Re: Internship application
Message-ID: <reply-1@globex.io>
In-Reply-To: <out-42@d.com>
References: <out-42@d.com>
Date: Wed, 12 Aug 2026 10:30:00 +0000
Content-Type: text/plain; charset="utf-8"

Thanks for reaching out! Happy to chat next week.
"""

RAW_NO_MSGID = b"""From: nobody@x.io
Subject: hi
Date: Wed, 12 Aug 2026 10:30:00 +0000
Content-Type: text/plain

body here
"""


def test_parse_message_extracts_headers_and_body():
    fm = parse_message(42, RAW_REPLY)
    assert isinstance(fm, FetchedMessage)
    assert fm.uid == 42
    assert fm.imap_message_id == "<reply-1@globex.io>"
    assert fm.from_addr == "priya@globex.io"
    assert fm.subject == "Re: Internship application"
    assert fm.in_reply_to == "<out-42@d.com>"
    assert fm.references == ["<out-42@d.com>"]
    assert "Happy to chat" in fm.body_text
    assert fm.received_at is not None and fm.received_at.year == 2026


def test_parse_message_falls_back_to_uid_when_no_message_id():
    fm = parse_message(7, RAW_NO_MSGID)
    assert fm.imap_message_id == "uid:7"
    assert fm.from_addr == "nobody@x.io"
    assert "body here" in fm.body_text
    assert fm.in_reply_to == "" and fm.references == []
