from app.send.smtp_client import build_email


def test_build_email_sets_headers_and_body():
    msg = build_email(from_email="me@d.com", from_name="Me", to="you@x.io",
                      subject="Hi", body="Hello there", pdf_path=None)
    assert msg["To"] == "you@x.io"
    assert msg["Subject"] == "Hi"
    assert msg["From"] == "Me <me@d.com>"
    assert msg["Message-ID"]
    assert msg.get_content().strip() == "Hello there"


def test_build_email_no_name_uses_bare_address():
    msg = build_email(from_email="me@d.com", from_name="", to="you@x.io",
                      subject="Hi", body="b", pdf_path=None)
    assert msg["From"] == "me@d.com"


def test_build_email_casual_has_no_attachment():
    msg = build_email(from_email="me@d.com", from_name="Me", to="you@x.io",
                      subject="Hi", body="b", pdf_path=None)
    assert not list(msg.iter_attachments())


def test_build_email_formal_attaches_pdf(tmp_path):
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    msg = build_email(from_email="me@d.com", from_name="Me", to="you@x.io",
                      subject="Hi", body="b", pdf_path=str(pdf))
    attachments = list(msg.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "resume.pdf"
    assert attachments[0].get_content_type() == "application/pdf"


class _RecordingTransport:
    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)
        return msg["Message-ID"]


def test_recording_transport_returns_message_id():
    msg = build_email(from_email="me@d.com", from_name="Me", to="you@x.io",
                      subject="Hi", body="b", pdf_path=None)
    t = _RecordingTransport()
    returned = t.send(msg)
    assert returned == msg["Message-ID"]
    assert t.sent == [msg]
