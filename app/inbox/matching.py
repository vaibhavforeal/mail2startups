_BOUNCE_SENDERS = ("mailer-daemon", "postmaster")
_DSN_MARKERS = (
    "message/delivery-status",
    "report-type=delivery-status",
    "delivery status notification",
    "undelivered mail returned",
)


def match_reply(fetched, *, sent_by_message_id, contact_emails_by_startup):
    """Exact first: any id in In-Reply-To/References that is a key of
    sent_by_message_id → (startup_id, message_id, matched_smtp_id). Fallback:
    from_addr matches a contact of a startup with a SENT message →
    (startup_id, None, None). Else None."""
    for candidate in (fetched.in_reply_to, *fetched.references):
        mid = candidate.strip()
        if mid and mid in sent_by_message_id:
            startup_id, message_id = sent_by_message_id[mid]
            return startup_id, message_id, mid
    startup_id = contact_emails_by_startup.get(fetched.from_addr.lower())
    if startup_id is not None:
        return startup_id, None, None
    return None


def detect_bounce(fetched, *, sent_by_message_id):
    """A DSN/MAILER-DAEMON envelope (mailer-daemon/postmaster sender, or a
    delivery-status marker in the raw source) whose raw body carries one of our
    smtp_message_ids → (startup_id, message_id). Else None."""
    frm = fetched.from_addr.lower()
    raw_lower = fetched.raw.lower()
    is_dsn = (any(tok in frm for tok in _BOUNCE_SENDERS)
              or any(marker in raw_lower for marker in _DSN_MARKERS))
    if not is_dsn:
        return None
    for mid, (startup_id, message_id) in sent_by_message_id.items():
        if mid and mid in fetched.raw:
            return startup_id, message_id
    return None
