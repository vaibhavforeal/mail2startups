from datetime import datetime, timezone

from app.config import get_settings
from app.db import get_engine, init_db, make_session
from app.models import (
    CampaignState, InboxKind, InboxMessage, ReplyLabel, Startup, StartupStatus,
)


def _session():
    engine = get_engine(":memory:")
    init_db(engine)
    return make_session(engine)


def test_enum_values():
    assert [k.value for k in InboxKind] == ["reply", "bounce"]
    assert [l.value for l in ReplyLabel] == [
        "interested", "rejection", "auto_reply", "other"]


def test_inbox_message_roundtrip():
    with _session() as s:
        startup = Startup(name="A", domain="a.io", source="yc",
                          status=StartupStatus.SENT)
        s.add(startup); s.commit()
        im = InboxMessage(
            startup_id=startup.id, message_id=None, kind=InboxKind.REPLY,
            imap_message_id="<abc@x>", imap_uid=42, from_addr="c@a.io",
            subject="Re: hi", snippet="hello", label=ReplyLabel.INTERESTED,
            matched_message_id="<out@d.com>",
            received_at=datetime(2026, 8, 12, tzinfo=timezone.utc))
        s.add(im); s.commit()
        got = s.get(InboxMessage, im.id)
        assert got.kind is InboxKind.REPLY
        assert got.label is ReplyLabel.INTERESTED
        assert got.imap_message_id == "<abc@x>" and got.imap_uid == 42
        assert got.label is not None and got.created_at is not None


def test_campaign_state_uid_watermark_defaults():
    with _session() as s:
        st = CampaignState(id=1)
        s.add(st); s.commit()
        assert st.last_imap_uid == 0 and st.imap_uidvalidity == 0


def test_settings_inbox_defaults():
    s = get_settings()
    assert s.imap_host == "imap.hostinger.com"
    assert s.imap_port == 993
    assert s.imap_mailbox == "INBOX"
    assert s.no_response_days == 14
