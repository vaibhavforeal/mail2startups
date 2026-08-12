import smtplib
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Contact, Draft, DraftMode, DraftStatus, Event, Message, MessageStatus,
    MessageType, Startup, StartupStatus,
)
from app.send import state as state_mod
from app.send.pacing import budget_remaining, is_within_window
from app.send.smtp_client import build_email

_FAILURE_PAUSE_THRESHOLD = 3


@dataclass
class SendResult:
    draft_id: int
    sent: bool
    reason: str | None = None


def approve_drafts(session: Session, ids: list[int] | None = None, *,
                   all_pending: bool = False) -> int:
    """Approve drafts by id (or every pending_review draft). Moves each
    Draft→approved and its Startup→queued. Returns the count approved.
    A draft whose startup has since replied or bounced is skipped — a reply
    landing while a follow-up awaited review must not be re-contacted."""
    query = select(Draft).where(Draft.status == DraftStatus.PENDING_REVIEW)
    if not all_pending:
        query = query.where(Draft.id.in_(ids or []))
    drafts = session.scalars(query).all()
    approved = 0
    for draft in drafts:
        startup = session.get(Startup, draft.startup_id)
        if startup is not None and startup.status in (
                StartupStatus.REPLIED, StartupStatus.BOUNCED):
            session.add(Event(startup_id=draft.startup_id, kind="approve_skipped",
                              payload={"draft_id": draft.id,
                                       "reason": startup.status.value}))
            continue
        draft.status = DraftStatus.APPROVED
        if startup is not None:
            startup.status = StartupStatus.QUEUED
        session.add(Event(startup_id=draft.startup_id, kind="approved",
                          payload={"draft_id": draft.id}))
        approved += 1
    session.commit()
    return approved


def reject_drafts(session: Session, ids: list[int]) -> int:
    """Reject drafts by id. Moves each Draft→rejected and its Startup→dead."""
    drafts = session.scalars(
        select(Draft).where(Draft.id.in_(ids),
                            Draft.status == DraftStatus.PENDING_REVIEW)).all()
    for draft in drafts:
        draft.status = DraftStatus.REJECTED
        startup = session.get(Startup, draft.startup_id)
        if startup is not None:
            startup.status = StartupStatus.DEAD
        session.add(Event(startup_id=draft.startup_id, kind="rejected",
                          payload={"draft_id": draft.id}))
    session.commit()
    return len(drafts)


def _eligible_drafts(session: Session, limit: int) -> list[Draft]:
    return session.scalars(
        select(Draft)
        .join(Startup, Draft.startup_id == Startup.id)
        .where(Draft.status == DraftStatus.APPROVED,
               Startup.status == StartupStatus.QUEUED,
               Draft.id.not_in(select(Message.draft_id)))
        .order_by(Draft.id)
        .limit(limit)
    ).all()


def send_batch(session: Session, *, now, transport, settings, limit: int = 1,
               dry_run: bool = False, force: bool = False) -> list[SendResult]:
    state = state_mod.ensure_state(session)
    if state.paused and not dry_run:
        return [SendResult(0, False, "paused")]
    if not dry_run and not force and not is_within_window(
            now, start_hhmm=settings.send_start, end_hhmm=settings.send_end,
            tz=settings.send_timezone):
        return [SendResult(0, False, "outside_window")]

    results: list[SendResult] = []
    for draft in _eligible_drafts(session, limit):
        if not dry_run and not force and budget_remaining(
                session, now, state.first_send_at, daily_cap=settings.daily_cap,
                ramp_cap=settings.ramp_daily_cap, ramp_days=settings.ramp_days,
                tz=settings.send_timezone) <= 0:
            results.append(SendResult(draft.id, False, "cap_reached"))
            break
        results.append(_send_one(session, draft, now=now, transport=transport,
                                 settings=settings, dry_run=dry_run))
    return results


def _send_one(session: Session, draft: Draft, *, now, transport, settings,
              dry_run: bool) -> SendResult:
    sid, did = draft.startup_id, draft.id
    contact = session.get(Contact, draft.contact_id) if draft.contact_id else None
    to_addr = settings.test_recipient if dry_run else (contact.email if contact else None)
    if not to_addr:
        # No usable recipient: mark the startup dead so it leaves the eligible
        # set — otherwise a limit=1 queue re-selects this same draft forever.
        startup = session.get(Startup, sid)
        if startup is not None:
            startup.status = StartupStatus.DEAD
        session.add(Event(startup_id=sid, kind="send_failed",
                          payload={"draft_id": did, "reason": "no_recipient"}))
        session.commit()
        return SendResult(did, False, "no_recipient")

    in_reply_to = None
    if draft.type == MessageType.FOLLOWUP:
        in_reply_to = session.scalar(
            select(Message.smtp_message_id)
            .join(Draft, Message.draft_id == Draft.id)
            .where(Draft.startup_id == sid,
                   Message.type == MessageType.INITIAL,
                   Message.smtp_message_id.is_not(None))
            .order_by(Message.id).limit(1))

    msg = build_email(
        from_email=settings.from_email or settings.smtp_user,
        from_name=settings.from_name, to=to_addr, subject=draft.subject,
        body=draft.body,
        pdf_path=draft.resume_pdf_path if draft.mode == DraftMode.FORMAL else None,
        in_reply_to=in_reply_to, references=in_reply_to)
    try:
        message_id = transport.send(msg)
    except (smtplib.SMTPException, OSError) as exc:
        session.rollback()
        session.add(Event(startup_id=sid, kind="send_failed",
                          payload={"draft_id": did, "reason": "smtp_error",
                                   "detail": str(exc)}))
        session.commit()
        if state_mod.record_failure(session) >= _FAILURE_PAUSE_THRESHOLD:
            state_mod.pause(session, "auto: 3 consecutive send failures")
        return SendResult(did, False, "smtp_error")

    if dry_run:
        session.add(Event(startup_id=sid, kind="dry_run_send",
                          payload={"draft_id": did, "to": to_addr}))
        session.commit()
        return SendResult(did, True, "dry_run")

    session.add(Message(draft_id=did, type=draft.type, sent_at=now,
                        smtp_message_id=message_id, status=MessageStatus.SENT))
    startup = session.get(Startup, sid)
    if startup is not None:
        startup.status = StartupStatus.SENT
    session.add(Event(startup_id=sid, kind="sent",
                      payload={"draft_id": did, "message_id": message_id}))
    session.commit()
    state_mod.record_success(session)
    return SendResult(did, True, None)


def send_test_emails(session: Session, *, transport, settings, count: int = 5) -> int:
    """Send `count` canned emails to settings.test_recipient to confirm auth and
    inbox placement. Does not touch drafts/startups."""
    to_addr = settings.test_recipient
    from_email = settings.from_email or settings.smtp_user
    sent = 0
    for i in range(count):
        msg = build_email(from_email=from_email, from_name=settings.from_name,
                          to=to_addr, subject=f"Mail2Startups test {i + 1}/{count}",
                          body="Deliverability test send. Safe to ignore.",
                          pdf_path=None)
        try:
            transport.send(msg)
        except (smtplib.SMTPException, OSError):
            break
        sent += 1
    session.add(Event(startup_id=None, kind="test_send",
                      payload={"requested": count, "sent": sent}))
    session.commit()
    return sent
