import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path
from typing import Protocol


def build_email(*, from_email: str, from_name: str, to: str, subject: str,
                body: str, pdf_path: str | None) -> EmailMessage:
    """Build a plain-text email; attach the PDF when pdf_path is set.
    Sets a generated Message-ID header for later reply matching."""
    msg = EmailMessage()
    msg["From"] = formataddr((from_name, from_email)) if from_name else from_email
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid()
    msg.set_content(body)
    if pdf_path:
        data = Path(pdf_path).read_bytes()
        msg.add_attachment(data, maintype="application", subtype="pdf",
                           filename=Path(pdf_path).name)
    return msg


class Transport(Protocol):
    def send(self, msg: EmailMessage) -> str:
        """Send msg; return its Message-ID."""
        ...


class SmtpTransport:
    """Real SMTP_SSL transport. Not exercised in tests (they inject a fake)."""

    def __init__(self, host: str, port: int, user: str, password: str):
        self._host = host
        self._port = port
        self._user = user
        self._password = password

    def send(self, msg: EmailMessage) -> str:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(self._host, self._port, context=context,
                              timeout=30) as smtp:
            smtp.login(self._user, self._password)
            smtp.send_message(msg)
        return msg["Message-ID"]
