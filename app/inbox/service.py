from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.inbox.matching import detect_bounce, match_reply
from app.models import (
    Contact, Draft, Event, InboxKind, InboxMessage, Message, MessageStatus,
    ReplyLabel, Startup, StartupStatus,
)
from app.send import state as state_mod


@dataclass
class InboxResult:
    replies: int
    bounces: int
    no_response: int
    fetched: int


def _as_utc(dt: datetime) -> datetime:
    """SQLite round-trips DateTime(timezone=True) as tz-naive. A naive value read
    back is UTC (we always store UTC), so pin it before comparing to an aware now
    — otherwise the comparison misreads it as host-local time."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _sent_maps(session: Session):
    """Build (sent_by_message_id, contact_emails_by_startup) from the DB."""
    rows = session.execute(
        select(Message.smtp_message_id, Message.id, Draft.startup_id)
        .join(Draft, Message.draft_id == Draft.id)
        .where(Message.smtp_message_id.is_not(None))
    ).all()
    sent_by_message_id = {smtp: (sid, mid) for smtp, mid, sid in rows}
    sent_startup_ids = {sid for _, _, sid in rows}
    contact_rows = session.execute(
        select(Contact.email, Contact.startup_id)
        .where(Contact.startup_id.in_(sent_startup_ids),
               Contact.email.is_not(None))
    ).all()
    contact_emails_by_startup = {
        email.lower(): sid for email, sid in contact_rows if email}
    return sent_by_message_id, contact_emails_by_startup


def _newest_sent_at(session: Session, startup_id: int):
    return session.scalar(
        select(func.max(Message.sent_at))
        .join(Draft, Message.draft_id == Draft.id)
        .where(Draft.startup_id == startup_id,
               Message.status == MessageStatus.SENT))


def _record_bounce(session, fetched, startup_id, message_id):
    matched_smtp = None
    msg = session.get(Message, message_id) if message_id else None
    if msg is not None:
        msg.status = MessageStatus.BOUNCED
        matched_smtp = msg.smtp_message_id
        draft = session.get(Draft, msg.draft_id)
        if draft is not None and draft.contact_id is not None:
            contact = session.get(Contact, draft.contact_id)
            if contact is not None:
                contact.verified = False
    startup = session.get(Startup, startup_id)
    if startup is not None:
        startup.status = StartupStatus.BOUNCED
    im = InboxMessage(
        startup_id=startup_id, message_id=message_id, kind=InboxKind.BOUNCE,
        imap_message_id=fetched.imap_message_id, imap_uid=fetched.uid,
        from_addr=fetched.from_addr, subject=fetched.subject,
        snippet=fetched.body_text[:500], label=None,
        matched_message_id=matched_smtp, received_at=fetched.received_at)
    session.add(im)
    session.flush()
    session.add(Event(startup_id=startup_id, kind="bounce",
                      payload={"inbox_message_id": im.id, "message_id": message_id}))


def _record_reply(session, fetched, startup_id, message_id, matched, label):
    msg = session.get(Message, message_id) if message_id else None
    if msg is not None:
        msg.status = MessageStatus.REPLIED
    startup = session.get(Startup, startup_id)
    if startup is not None:
        startup.status = StartupStatus.REPLIED
    im = InboxMessage(
        startup_id=startup_id, message_id=message_id, kind=InboxKind.REPLY,
        imap_message_id=fetched.imap_message_id, imap_uid=fetched.uid,
        from_addr=fetched.from_addr, subject=fetched.subject,
        snippet=fetched.body_text[:500], label=label,
        matched_message_id=matched, received_at=fetched.received_at)
    session.add(im)
    session.flush()
    session.add(Event(startup_id=startup_id, kind="reply",
                      payload={"inbox_message_id": im.id, "message_id": message_id,
                               "label": label.value}))


def _sweep_no_response(session, now, no_response_days, *, mutate):
    cutoff = now - timedelta(days=no_response_days)
    due = []
    for s in session.scalars(
            select(Startup).where(Startup.status == StartupStatus.SENT)).all():
        newest = _newest_sent_at(session, s.id)
        if newest is None:
            continue
        if _as_utc(newest) < cutoff:
            due.append(s)
    if mutate:
        for s in due:
            s.status = StartupStatus.NO_RESPONSE
            session.add(Event(startup_id=s.id, kind="no_response", payload={}))
    return len(due)


def poll_inbox(session: Session, *, imap, classifier, now, settings,
               limit: int | None = None, dry_run: bool = False) -> InboxResult:
    state = state_mod.ensure_state(session)
    uidvalidity, msgs = imap.fetch_new(
        settings.imap_mailbox, state.last_imap_uid, state.imap_uidvalidity)
    if limit is not None:
        msgs = msgs[:limit]
    fetched = len(msgs)

    sent_by_message_id, contact_emails_by_startup = _sent_maps(session)
    replies = bounces = 0
    max_uid = state.last_imap_uid

    for fm in msgs:
        max_uid = max(max_uid, fm.uid)
        # Dedup on the inbound Message-ID (autoflush makes a same-batch add visible).
        if session.scalar(select(InboxMessage.id).where(
                InboxMessage.imap_message_id == fm.imap_message_id)) is not None:
            continue

        bounce = detect_bounce(fm, sent_by_message_id=sent_by_message_id)
        if bounce is not None:
            startup_id, message_id = bounce
            startup = session.get(Startup, startup_id)
            if startup is not None and startup.status in (
                    StartupStatus.REPLIED, StartupStatus.BOUNCED):
                continue
            bounces += 1
            if not dry_run:
                _record_bounce(session, fm, startup_id, message_id)
            continue

        rep = match_reply(fm, sent_by_message_id=sent_by_message_id,
                          contact_emails_by_startup=contact_emails_by_startup)
        if rep is None:
            continue
        startup_id, message_id, matched = rep
        startup = session.get(Startup, startup_id)
        if startup is not None and startup.status in (
                StartupStatus.REPLIED, StartupStatus.BOUNCED):
            continue
        label = classifier(fm.body_text)
        replies += 1
        if not dry_run:
            _record_reply(session, fm, startup_id, message_id, matched, label)

    no_response = _sweep_no_response(
        session, now, settings.no_response_days, mutate=not dry_run)

    if dry_run:
        session.rollback()
    else:
        state.last_imap_uid = max_uid
        state.imap_uidvalidity = uidvalidity
        session.commit()
    return InboxResult(replies, bounces, no_response, fetched)
