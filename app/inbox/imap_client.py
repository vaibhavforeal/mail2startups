import imaplib
from dataclasses import dataclass
from datetime import datetime
from email import message_from_bytes, policy
from email.utils import parseaddr, parsedate_to_datetime
from typing import Protocol


@dataclass
class FetchedMessage:
    uid: int
    imap_message_id: str          # inbound Message-ID header; f"uid:{uid}" if absent
    from_addr: str
    subject: str
    in_reply_to: str
    references: list[str]
    body_text: str
    raw: str
    received_at: datetime | None


def _body_text(msg) -> str:
    """First text/plain part (skipping attachments), decoded to str."""
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.get_content_type() != "text/plain":
            continue
        if part.get_content_disposition() == "attachment":
            continue
        try:
            return part.get_content().strip()
        except (LookupError, ValueError):
            payload = part.get_payload(decode=True) or b""
            return payload.decode("utf-8", "ignore").strip()
    return ""


def parse_message(uid: int, raw_bytes: bytes) -> FetchedMessage:
    msg = message_from_bytes(raw_bytes, policy=policy.default)
    message_id = str(msg["Message-ID"] or "").strip()
    references = str(msg["References"] or "").split()
    try:
        received = parsedate_to_datetime(msg["Date"]) if msg["Date"] else None
    except (TypeError, ValueError):
        received = None
    return FetchedMessage(
        uid=uid,
        imap_message_id=message_id or f"uid:{uid}",
        from_addr=parseaddr(str(msg["From"] or ""))[1].lower(),
        subject=str(msg["Subject"] or "").strip(),
        in_reply_to=str(msg["In-Reply-To"] or "").strip(),
        references=references,
        body_text=_body_text(msg),
        raw=raw_bytes.decode("utf-8", "ignore"),
        received_at=received,
    )


class ImapClient(Protocol):
    def fetch_new(self, mailbox: str, since_uid: int,
                  uidvalidity: int) -> tuple[int, list[FetchedMessage]]:
        ...


class HostingerImap:
    """Real IMAP4_SSL client. Not exercised in tests (they inject a fake).

    Opens the mailbox read-only (never sets \\Seen). On a UIDVALIDITY mismatch it
    ignores `since_uid` and returns everything (watermark reset). IMAP/network
    failures raise (imaplib.IMAP4.error, OSError) for the CLI to contain."""

    def __init__(self, host: str, port: int, user: str, password: str):
        self._host = host
        self._port = port
        self._user = user
        self._password = password

    def fetch_new(self, mailbox: str, since_uid: int,
                  uidvalidity: int) -> tuple[int, list[FetchedMessage]]:
        conn = imaplib.IMAP4_SSL(self._host, self._port)
        try:
            conn.login(self._user, self._password)
            conn.select(mailbox, readonly=True)
            raw_validity = conn.response("UIDVALIDITY")[1][0]
            server_uidvalidity = int(raw_validity) if raw_validity else 0
            start = since_uid + 1 if server_uidvalidity == uidvalidity else 1
            _, data = conn.uid("search", None, f"UID {start}:*")
            uids = [int(x) for x in (data[0] or b"").split()]
            # "UID n:*" returns the highest UID even when it is below n; filter.
            uids = [u for u in uids if u >= start]
            messages: list[FetchedMessage] = []
            for u in uids:
                _, msg_data = conn.uid("fetch", str(u), "(RFC822)")
                if not msg_data or not isinstance(msg_data[0], tuple):
                    continue
                messages.append(parse_message(u, msg_data[0][1]))
            return server_uidvalidity, messages
        finally:
            try:
                conn.logout()
            except (imaplib.IMAP4.error, OSError):
                pass
